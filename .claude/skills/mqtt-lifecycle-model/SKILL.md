---
name: mqtt-lifecycle-model
description: Model-check the AWS IoT MQTT connection lifecycle (connect, CONNACK, subscribe, credential renewal, reconnect, stale paho callbacks) with TLA+ and TLC. Use when a change touches SalusMqttConnection in custom_components/salus_it600_cloud/mqtt.py, when the mqtt-lifecycle-model CI job fails, or when a bug report involves commands failing after about an hour, commands sent before the connection is up, or push switching off by itself.
---

# MQTT lifecycle model

`formal/mqtt_lifecycle/MqttLifecycle.tla` is a TLA+ model of `SalusMqttConnection`. TLC explores every order in which the event loop, the worker threads, paho's network thread, the broker and the timers can take their steps, within the bounds below. Tests in `tests/test_mqtt.py` cover the orders someone wrote down. The model covers the rest.

TLA+ was chosen because every bug this guards against is a bad order of events between threads, not a wrong value for some input.

## Run

```sh
python3 formal/mqtt_lifecycle/check.py
```

It needs Java 11 or newer and Python 3.10 or newer. It downloads `tla2tools.jar` 1.7.4 once, checks its SHA-256, and takes about 20 seconds on a laptop. `--trace` also prints the path of every variant that is rejected as expected. Naming cases, as in `check.py current`, runs only those. CI runs the same command in the `mqtt-lifecycle-model` job.

Read every result with the pstack `/statechart` skill, giving it `MqttLifecycle.tla` and the case's `.cfg`. It tells each break as its shortest path in plain words, and draws the state chart when a case holds. The model stops creating clients and advancing time at its bounds, so a deadlock that `/statechart` reports there is the bound and not a bug. `check.py` turns the deadlock check off.

## What is checked

Each case in `formal/mqtt_lifecycle/cases/` starts with an `expect:` line. `current.cfg` is the code as it is and must hold. Every other case must break the invariant it names. A property nobody has seen fail proves nothing, so a broken variant that stops failing is an error too.

| Invariant | Promise | Harm when broken | Proven able to fail by |
| --- | --- | --- | --- |
| `PublishOnlyAfterConnack` | A command is published only on a connection whose CONNACK the event loop has seen. | Commands go out on a socket that is not an MQTT session yet and fail. | `bug-1b8ce48-connected-before-connack` |
| `NoPublishOnExpiredCredentials` | A command is never published on a connection whose AWS credentials have expired. | Every command fails with rc=7 about an hour after start, until Home Assistant restarts. | `bug-ac5b440-no-renewal-before-expiry`, `broken-zero-renew-margin` |
| `NoPublishOnDroppedConnection` | A command is published only on a connection the event loop has not yet been told is lost. | A command sent while the connection comes and goes fails at once and is not sent on the next connection. | `bug-connected-after-loss`, `bug-command-resumes-after-loss` |
| `PushVerdictFromCurrentClient` | Only callbacks of the current client decide whether push works. | The disconnect of a client that was stopped after its subscription failed switches push off for good. | `broken-no-stale-client-guard` |
| `PushVerdictFromItsOwnSubscription` | Push is declared denied only because of a subscription made on the same connection. | A connection that drops before it subscribes is taken as a refusal by the AWS IoT policy, and push stays off until Home Assistant restarts. | `bug-stale-subscribing-flag` |
| `PushDefinite` | Once every SUBSCRIBE of a connection is answered, `push_available` is `True` or `False`. | The coordinator never learns that push works and keeps the fast polling. | `broken-no-evaluate-after-send` |

A `bug-<commit>-` case replays a bug that the named commit fixed, by setting the switch for that fix to `FALSE`. A `broken-` case removes something that never was missing.

## Open findings

`finding-idle-loss-is-a-refusal`: a connection lost while no SUBSCRIBE awaits an answer is taken as a refusal by the AWS IoT policy, and push stays off until Home Assistant restarts. Its test is `test_connection_lost_before_any_subscribe_was_sent_does_not_disable_push`.

A bug the model finds and nobody was asked to fix is recorded as an open finding: a `finding-` case that expects the break of an invariant `current.cfg` does not list yet, and a test in `tests/test_mqtt.py` marked `xfail(strict=True)`, so that it cannot change unnoticed. Once it is fixed, add a switch whose `FALSE` is the old behaviour, rename the case to `bug-<commit>-`, add the invariant to `current.cfg` and remove the marker.

Put every invariant into `current.cfg` as soon as its finding is fixed. A finding case shows only the shortest path to the break, and one invariant can break in several ways: `NoPublishOnDroppedConnection` needed two fixes, and the second path showed up only when `current.cfg` had to keep the invariant.

## Does a change touch the model

The model covers these functions of `mqtt.py`. Every action in the `.tla` file names the function and lines it stands for.

| Function | Actions |
| --- | --- |
| `_run` | `RunLost`, `RunRenewalDue`, `RunStopReturned`, `RunSleepOver`, `RenewalTimerFires`, `SleepEnds`, and the `Fail` and `EnterWait` pieces |
| `_async_connect`, `_connect_sync` | `RunCredentials`, `RunCredentialsFailed`, `RunConnectReturned`, `RunConnackArrived`, `ThreadConnects`, `ConnackTimesOut`, and the `Accepted` and `PostConnack` pieces |
| `_async_subscribe` | `PostConnack`, `ThreadSubscribes`, `RunSubscribeReturned` |
| `_async_stop_client`, `_stop_sync` | `StopClient`, `ThreadStops` |
| `_on_connect`, `_handle_connect` | `BrokerAccepts`, `BrokerRefuses`, `HandleConnect` |
| `_on_disconnect`, `_handle_disconnect` | `ConnectionDrops`, `PahoReportsDisconnect`, `ThreadStops`, `HandleDisconnect` |
| `_on_subscribe`, `_handle_subscribe`, `_evaluate_subscriptions`, `_set_push_available` | `BrokerAnswersSubscribe`, `HandleSubscribe`, `Evaluate` |
| `async_publish_shadow` | `PublishCalled`, `PublishWoken` |
| `__init__` | the variables |

The model was last confirmed against these functions in the last commit that changed `formal/mqtt_lifecycle/covered.json`. Find it with `git log -1 --format=%h -- formal/mqtt_lifecycle/covered.json`.

You do not have to remember this table. `check.py` fails when a covered function differs from the digest in `formal/mqtt_lifecycle/covered.json`, or when a line reference in the model no longer falls inside its function.

## When a covered function changed

`check.py` names the functions that changed and the ones that only moved.

1. Read the diff of each changed function and the actions that refer to it. One action is one indivisible step: an event-loop callback, or a task step from one `await` to the next. A new `await` splits an action. A removed one merges two.
2. Change those actions and their line references to describe the new code. Keep the switches (`RenewBeforeExpiry` and the others) meaning "the code as written" when `TRUE`. Leave the references of functions that only moved alone.
3. Run `check.py`. It still reports the changed functions, and the case results now speak for the new code.
4. When every case gives its expected result, and you have confirmed that the model describes the code, run `python3 formal/mqtt_lifecycle/check.py --update-lock`, and commit the lock with the model. The command records the new digests and shifts the line references of the functions that only moved.

## When an invariant breaks

`check.py` prints the path as numbered steps, each the name of an action and the variables it changed. Replay it against `mqtt.py`, one action at a time, using the line references.

- A step cannot happen in the code: the code has a check the action lacks, or two steps of the path are one indivisible step in the code. The model is wrong. Correct the action.
- Every step can happen: it is a bug in `mqtt.py`. Reproduce it as a test in `tests/test_mqtt.py` with the `_Broker` fake, whose `failing_connects` scripts drops and refusals. Report it with the path. Do not weaken the invariant, loosen `Tick`, or remove an environment action to make the case hold, and do not change `mqtt.py` unless the user asked for the fix.

To guard a new promise, add the invariant, add a switch whose `FALSE` breaks it, and add a case that expects the break.

## Bounds and assumptions

A pass covers the model within these bounds, not the running integration.

- Two paho clients, two SUBSCRIBE batches per connection, one automatic paho reconnect per client, one caller of `async_publish_shadow`, credentials that live two ticks with a margin of one, three ticks in total.
- asyncio runs callbacks in the order they were registered, and `loop_stop()` joins paho's thread, so callbacks of a stopped client reach the loop before `_async_stop_client` returns.
- paho closes a lost socket before it reports the loss, so a failed `client.subscribe` and the disconnect callback reach the loop in either order.
- A connect, a stop and a queued callback take much less time than `renew_margin`. Time passes only while the loop is idle and `_run` waits or sleeps.
- The credentials provider returns credentials that expire later than `renew_margin` from now.

Not covered: `async_stop`, `set_device_codes`, an empty device list, `_handle_message`, the length of the reconnect backoff, the SigV4 URL, whether a publish is confirmed, and anything in `coordinator.py`. `_handle_disconnect` takes every loss of the subscribing connection as a refusal by the AWS IoT policy, also when no SUBSCRIBE had been sent yet, and no invariant asks whether that guess is right. The invariants are safety properties. Nothing here proves that the connection eventually recovers. Fetching the event loop from paho's thread (the bug fixed by `9b09537`) is outside the model.

`/maintain-formal-verification-skill` brings the model back in line after changes that were merged without rechecking it.
