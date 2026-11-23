---------------------------- MODULE MqttLifecycle ----------------------------
(***************************************************************************)
(* Lifecycle of SalusMqttConnection in                                     *)
(* custom_components/salus_it600_cloud/mqtt.py.                            *)
(*                                                                         *)
(* One step of this model is one indivisible step of the code: either one  *)
(* event-loop callback or task step between two awaits (the Handle*, Run*  *)
(* and Publish* actions), or one thing a worker thread, paho's network     *)
(* thread, the broker or a timer does on its own.  asyncio runs callbacks  *)
(* in the order they were registered, so everything that reaches the loop  *)
(* goes through the single FIFO `queue`.                                   *)
(*                                                                         *)
(* Every action names the lines and the function of mqtt.py it stands for. *)
(* check.py fails when one of these references no longer points inside     *)
(* that function, or when a referenced function changed.                   *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS
    MaxClients,         \* paho clients created before the model stops connecting
    Batches,            \* SUBSCRIBE requests per connection (mqtt.py:221 _async_subscribe)
    Lifetime,           \* ticks from issuing credentials to their expiry
    Margin,             \* renew_margin in ticks (mqtt.py:172 _run)
    MaxTime,
    MaxPahoReconnects,  \* automatic reconnects of one paho client (paho loop_forever)
    \* The switches below are TRUE for the code as written.  Each FALSE is a
    \* broken variant that one case in cases/ expects the checker to reject.
    RenewBeforeExpiry,  \* mqtt.py:174 _run waits for a lost connection only until renewal is due
    WaitForConnack,     \* mqtt.py:196-199 _async_connect waits for CONNACK before _connected.set()
    StaleClientGuard,   \* the `client is self._client` tests in the _handle_* methods
    EvaluateAfterSend,  \* mqtt.py:229 _async_subscribe evaluates SUBACKs that arrived early
    RefuseLostConnection,   \* mqtt.py:200-201 _async_connect raises when the connection is already lost
    RecheckAfterWake,       \* mqtt.py:138 async_publish_shadow looks at _connected again when it resumes
    SubscribingPerClient,   \* mqtt.py:270 _handle_disconnect asks whether the lost client is the one subscribing
    \* mqtt.py:282 _evaluate_subscriptions takes a loss as a refusal only while a SUBSCRIBE is unanswered
    RefuseOnlyUnanswered

VARIABLES
    \* Fields of SalusMqttConnection (mqtt.py:100-112 __init__); client 0 is None.
    \* `subscribing` is _subscribing_client.
    client, connected, lost, connack,
    pushAvailable, pushDenied, subscribing, subsSent, expected, subacks,
    \* The _run task: where it is suspended, its local `client`, and the
    \* thread call, timer or request it is waiting for.
    pc, tc, batch, job, jobOk, jobMid,
    \* One caller of async_publish_shadow.
    ppc,
    queue,
    \* The world outside the event loop.
    nextClient, sock, credExpiry, nextMid, brokerSubs, reconnects, now,
    \* Bookkeeping that the code does not have.  It records what the event
    \* loop has been told, so that the properties can be stated.
    known, subClient, verdictBy, publishedOn

connVars  == <<client, connected, lost, connack>>
pushVars  == <<pushAvailable, pushDenied, subscribing, subsSent, expected, subacks>>
taskVars  == <<pc, tc, batch, job, jobOk, jobMid>>
envVars   == <<nextClient, sock, credExpiry, nextMid, brokerSubs, reconnects, now>>
ghostVars == <<known, subClient, verdictBy, publishedOn>>
vars      == <<connVars, pushVars, taskVars, ppc, queue, envVars, ghostVars>>

Clients == 1..MaxClients
Mids    == 1..Batches

Entry(k, c, m, ok) == [k |-> k, c |-> c, m |-> m, ok |-> ok]
Wake(k) == Entry(k, 0, 0, TRUE)

Head1(k) == queue # <<>> /\ Head(queue).k = k
q == Tail(queue)

Current(c) == StaleClientGuard => c = client

NoSubacks == [m \in Mids |-> "none"]

Init ==
    /\ client = 0 /\ connected = FALSE /\ lost = FALSE /\ connack = "none"
    /\ pushAvailable = "unknown" /\ pushDenied = FALSE
    /\ subscribing = 0 /\ subsSent = FALSE /\ expected = {} /\ subacks = NoSubacks
    /\ pc = "await_creds" /\ tc = 0 /\ batch = 0
    /\ job = "queued" /\ jobOk = FALSE /\ jobMid = 0
    /\ ppc = "idle"
    /\ queue = <<>>
    /\ nextClient = 1
    /\ sock = [c \in Clients |-> "new"]
    /\ credExpiry = [c \in Clients |-> 0]
    /\ nextMid = [c \in Clients |-> 0]
    /\ brokerSubs = {}
    /\ reconnects = [c \in Clients |-> 0]
    /\ now = 0
    /\ known = [c \in Clients |-> "unacked"]
    /\ subClient = 0 /\ verdictBy = "none" /\ publishedOn = "none"

-----------------------------------------------------------------------------
(* Pieces of event-loop steps.  Each sets only the variables listed.       *)

\* mqtt.py:231-235 _async_stop_client.  Sets client, connected, pc, job, tc.
StopClient(stopPc, idlePc) ==
    /\ client' = 0
    /\ connected' = FALSE
    /\ job' = "queued"
    /\ IF client = 0
           THEN pc' = idlePc /\ tc' = tc
           ELSE pc' = stopPc /\ tc' = client

\* mqtt.py:165-170 _run, the `except Exception` branch.
Fail == StopClient("stop_sleep", "sleep")

\* mqtt.py:172-182 _run.  An Event that is already set does not suspend the
\* task, so a connection lost during the connect is stopped in the same step.
\* Sets client, connected, pc, job, tc.
EnterWait(connectedIfWaiting) ==
    IF lost
        THEN StopClient("stop_sleep", "sleep")
        ELSE /\ pc' = "wait_lost"
             /\ job' = IF RenewBeforeExpiry THEN "queued" ELSE "none"
             /\ connected' = connectedIfWaiting
             /\ UNCHANGED <<client, tc>>

\* Who decided about push, for the properties.  `kind` stands when the verdict
\* comes from the current client and its own subscription.
VerdictOf(by, kind) ==
    IF by # client THEN "stale_client"
    ELSE IF subClient # by THEN "stale_flag"
    ELSE kind

\* A lost connection is a refusal by the AWS IoT policy only while a SUBSCRIBE
\* that paho took for that connection has no answer the event loop has seen.
LossKind(c, acks) ==
    IF \E m \in 1..nextMid[c] : acks[m] = "none" THEN "drop" ELSE "idle_drop"

\* mqtt.py:278-290 _evaluate_subscriptions and mqtt.py:292-302 _set_push_available.
\* Sets subscribing, pushAvailable, pushDenied, verdictBy.
Evaluate(runs, acks, sent, mids, isLost, by) ==
    LET unanswered == \E m \in mids : acks[m] = "none"
        refused == RefuseOnlyUnanswered /\ isLost /\ unanswered
        done    == runs /\ subscribing # 0 /\ (refused \/ (sent /\ ~unanswered))
        granted == ~refused /\ \A m \in mids : acks[m] = "ok"
    IN  /\ subscribing' = IF done THEN 0 ELSE subscribing
        /\ pushAvailable' = IF done THEN (IF granted THEN "yes" ELSE "no") ELSE pushAvailable
        /\ pushDenied' = (pushDenied \/ (done /\ ~granted))
        /\ verdictBy' = IF ~done THEN verdictBy
                        ELSE VerdictOf(by, IF refused THEN LossKind(by, acks) ELSE "suback")

\* mqtt.py:202-206 _async_connect and mqtt.py:216-220 _async_subscribe, up to
\* the first await.  Sets queue, ppc, client, connected, pc, job, tc, batch,
\* subClient and all of pushVars.
PostConnack ==
    /\ queue' = IF ppc = "waiting" THEN Append(q, Wake("wake_pub")) ELSE q
    /\ ppc' = IF ppc = "waiting" THEN "woken" ELSE ppc
    /\ IF pushDenied
           THEN /\ EnterWait(TRUE)
                /\ UNCHANGED <<pushVars, batch, subClient>>
           ELSE /\ connected' = TRUE
                /\ subscribing' = tc
                /\ subsSent' = FALSE
                /\ expected' = {}
                /\ subacks' = NoSubacks
                /\ subClient' = tc
                /\ batch' = 1
                /\ pc' = "subscribe"
                /\ job' = "queued"
                /\ UNCHANGED <<client, tc, pushAvailable, pushDenied>>

\* mqtt.py:200-206 _async_connect, once the CONNACK is known to be a success.
\* The disconnect of this client may have been handled before the task resumed.
Accepted ==
    IF RefuseLostConnection /\ lost
        THEN /\ Fail
             /\ queue' = q
             /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc,
                            envVars, ghostVars>>
        ELSE /\ PostConnack
             /\ UNCHANGED <<lost, connack, jobOk, jobMid, envVars,
                            known, verdictBy, publishedOn>>

Classify(c) ==
    IF known[c] = "unacked" THEN "unacked"
    ELSE IF known[c] = "dropped" THEN "dropped"
    ELSE IF now >= credExpiry[c] THEN "expired"
    ELSE "live"

\* mqtt.py:142-147 async_publish_shadow, once _connected is seen set.
PubTake ==
    /\ ppc' = "idle"
    /\ publishedOn' = IF client = 0 THEN publishedOn ELSE Classify(client)

-----------------------------------------------------------------------------
(* Callbacks that paho's network thread handed to the event loop.          *)

\* mqtt.py:261-263 _handle_connect, queued by mqtt.py:246-248 _on_connect.
HandleConnect ==
    /\ Head1("on_connect")
    /\ LET e == Head(queue)
           resolves == Current(e.c) /\ connack = "pending"
       IN  /\ known' = IF e.ok THEN [known EXCEPT ![e.c] = "live"] ELSE known
           /\ connack' = IF ~resolves THEN connack ELSE IF e.ok THEN "ok" ELSE "refused"
           /\ queue' = IF resolves /\ pc = "await_connack" THEN Append(q, Wake("connack")) ELSE q
    /\ UNCHANGED <<client, connected, lost, pushVars, taskVars, ppc, envVars,
                   subClient, verdictBy, publishedOn>>

\* mqtt.py:265-271 _handle_disconnect, queued by mqtt.py:250-251 _on_disconnect.
\* Without RefuseOnlyUnanswered every loss of the subscribing client is a refusal.
HandleDisconnect ==
    /\ Head1("on_disconnect")
    /\ LET e == Head(queue)
           applies == Current(e.c)
           asks    == applies /\ IF SubscribingPerClient THEN subscribing = e.c
                                                         ELSE subscribing # 0
       IN  /\ known' = [known EXCEPT ![e.c] = IF @ = "live" THEN "dropped" ELSE @]
           /\ connected' = IF applies THEN FALSE ELSE connected
           /\ lost' = IF applies THEN TRUE ELSE lost
           /\ IF RefuseOnlyUnanswered
                  THEN Evaluate(asks, subacks, subsSent, expected, TRUE, e.c)
                  ELSE /\ subscribing' = IF asks THEN 0 ELSE subscribing
                       /\ pushAvailable' = IF asks THEN "no" ELSE pushAvailable
                       /\ pushDenied' = (pushDenied \/ asks)
                       /\ verdictBy' = IF ~asks THEN verdictBy
                                       ELSE VerdictOf(e.c, LossKind(e.c, subacks))
           /\ queue' = IF applies /\ ~lost /\ pc = "wait_lost" THEN Append(q, Wake("lost")) ELSE q
    /\ UNCHANGED <<client, connack, subsSent, expected, subacks, taskVars, ppc,
                   envVars, subClient, publishedOn>>

\* mqtt.py:273-276 _handle_subscribe, queued by mqtt.py:253-255 _on_subscribe.
HandleSubscribe ==
    /\ Head1("on_subscribe")
    /\ LET e == Head(queue)
           applies == Current(e.c)
           acks == IF applies THEN [subacks EXCEPT ![e.m] = IF e.ok THEN "ok" ELSE "failed"]
                   ELSE subacks
       IN  /\ subacks' = acks
           /\ Evaluate(applies, acks, subsSent, expected, lost, e.c)
    /\ queue' = q
    /\ UNCHANGED <<connVars, subsSent, expected, taskVars, ppc, envVars,
                   known, subClient, publishedOn>>

-----------------------------------------------------------------------------
(* Steps of the _run task, each from one await to the next.                *)

\* mqtt.py:185-195 _async_connect, from the credentials to to_thread(_connect_sync).
RunCredentials ==
    /\ Head1("creds")
    /\ client' = Head(queue).c
    /\ tc' = Head(queue).c
    /\ lost' = FALSE
    /\ connack' = "pending"
    /\ pc' = "connect_sync"
    /\ job' = "queued"
    /\ queue' = q
    /\ UNCHANGED <<connected, pushVars, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:185 _async_connect raises, mqtt.py:165-168 _run catches it.
RunCredentialsFailed ==
    /\ Head1("creds_err")
    /\ Fail
    /\ queue' = q
    /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:195-206 _async_connect, after to_thread(_connect_sync) returned.
\* Awaiting a future that is already done does not suspend the task.
RunConnectReturned ==
    /\ Head1("connect_ret")
    /\ IF ~jobOk \/ (WaitForConnack /\ connack = "refused")
           THEN /\ Fail
                /\ queue' = q
                /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc,
                               envVars, ghostVars>>
       ELSE IF ~WaitForConnack \/ connack = "ok"
           THEN Accepted
       ELSE /\ pc' = "await_connack"
            /\ queue' = q
            /\ UNCHANGED <<connVars, pushVars, tc, batch, job, jobOk, jobMid, ppc,
                           envVars, ghostVars>>

\* mqtt.py:196-206 _async_connect, woken by the CONNACK or by CONNECT_TIMEOUT.
RunConnackArrived ==
    /\ Head1("connack")
    /\ IF connack = "ok"
           THEN Accepted
           ELSE /\ Fail
                /\ queue' = q
                /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc,
                               envVars, ghostVars>>

\* mqtt.py:223-229 _async_subscribe, after to_thread(client.subscribe) returned.
\* A refused call leaves _subscribing_client set (mqtt.py:225 _async_subscribe).
\* Every batch is evaluated (mqtt.py:227 _async_subscribe), because the loss of
\* the connection can reach the event loop before the call that sent it returns.
RunSubscribeReturned ==
    /\ Head1("sub_ret")
    /\ queue' = q
    /\ LET mids == expected \cup {jobMid} IN
       IF ~jobOk
           THEN /\ Fail
                /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc,
                               envVars, ghostVars>>
       ELSE /\ expected' = mids
            /\ subsSent' = (batch = Batches)
            /\ Evaluate(TRUE, subacks, EvaluateAfterSend /\ batch = Batches, mids, lost, tc)
            /\ IF batch < Batches
                   THEN /\ batch' = batch + 1
                        /\ job' = "queued"
                        /\ UNCHANGED <<client, connected, pc, tc>>
                   ELSE /\ EnterWait(connected)
                        /\ UNCHANGED batch
            /\ UNCHANGED <<lost, connack, subacks, jobOk, jobMid, ppc, envVars,
                           known, subClient, publishedOn>>

\* mqtt.py:179-181 _run, the connection was lost.
RunLost ==
    /\ Head1("lost")
    /\ IF pc = "wait_lost"
           THEN StopClient("stop_sleep", "sleep")
           ELSE UNCHANGED <<client, connected, pc, job, tc>>
    /\ queue' = q
    /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:176-178 _run, the credentials are due for renewal.
RunRenewalDue ==
    /\ Head1("renew")
    /\ IF pc = "wait_lost"
           THEN StopClient("stop_renew", "await_creds")
           ELSE UNCHANGED <<client, connected, pc, job, tc>>
    /\ queue' = q
    /\ UNCHANGED <<lost, connack, pushVars, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:167-168 _run and mqtt.py:181-182 _run sleep after the stop,
\* mqtt.py:178 _run goes straight back to mqtt.py:164 _run.
RunStopReturned ==
    /\ Head1("stop_ret")
    /\ pc' = IF pc = "stop_renew" THEN "await_creds" ELSE "sleep"
    /\ job' = "queued"
    /\ queue' = q
    /\ UNCHANGED <<connVars, pushVars, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:168-170 _run and mqtt.py:182 _run, back to mqtt.py:164 _run.
RunSleepOver ==
    /\ Head1("sleep_done")
    /\ pc' = "await_creds"
    /\ job' = "queued"
    /\ queue' = q
    /\ UNCHANGED <<connVars, pushVars, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

-----------------------------------------------------------------------------
(* async_publish_shadow, called by the coordinator (coordinator.py          *)
(* _async_publish).                                                         *)

\* mqtt.py:135-147 async_publish_shadow.  With _connected set, the caller does
\* not suspend and the client is read in the same step.
PublishCalled ==
    /\ ppc = "idle"
    /\ IF connected
           THEN PubTake
           ELSE ppc' = "waiting" /\ UNCHANGED publishedOn
    /\ UNCHANGED <<connVars, pushVars, taskVars, queue, envVars, known, subClient, verdictBy>>

\* mqtt.py:137-147 async_publish_shadow, woken by _connected.set().  Event.wait()
\* does not look at the flag again after the wake-up, so the loop around it does.
PublishWoken ==
    /\ Head1("wake_pub")
    /\ IF RecheckAfterWake /\ ~connected
           THEN ppc' = "waiting" /\ UNCHANGED publishedOn
           ELSE PubTake
    /\ queue' = q
    /\ UNCHANGED <<connVars, pushVars, taskVars, envVars, known, subClient, verdictBy>>

-----------------------------------------------------------------------------
(* Worker threads started with asyncio.to_thread.                           *)

\* mqtt.py:209-213 _connect_sync.  AWS IoT rejects the WebSocket handshake of
\* expired credentials; any connect can also fail for other reasons.
ThreadConnects ==
    /\ pc = "connect_sync" /\ job = "queued"
    /\ \/ /\ now < credExpiry[tc]
          /\ sock' = [sock EXCEPT ![tc] = "connecting"]
          /\ jobOk' = TRUE
       \/ /\ jobOk' = FALSE
          /\ UNCHANGED sock
    /\ job' = "ran"
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobMid, ppc, queue,
                   nextClient, credExpiry, nextMid, brokerSubs, reconnects, now, ghostVars>>

\* mqtt.py:223 _async_subscribe.  paho returns MQTT_ERR_NO_CONN without a socket.
ThreadSubscribes ==
    /\ pc = "subscribe" /\ job = "queued"
    /\ IF sock[tc] \in {"connecting", "up"}
           THEN /\ nextMid' = [nextMid EXCEPT ![tc] = @ + 1]
                /\ jobMid' = nextMid[tc] + 1
                /\ jobOk' = TRUE
                /\ brokerSubs' = brokerSubs \cup {<<tc, nextMid[tc] + 1>>}
           ELSE /\ jobOk' = FALSE
                /\ UNCHANGED <<nextMid, jobMid, brokerSubs>>
    /\ job' = "ran"
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, ppc, queue,
                   nextClient, sock, credExpiry, reconnects, now, ghostVars>>

\* mqtt.py:238-242 _stop_sync.  loop_stop() joins paho's thread, so every
\* callback of the stopped client is queued before the thread call returns.
\* paho may or may not report the clean disconnect before it exits, and it
\* always reports a socket it has already closed.
ThreadStops ==
    /\ pc \in {"stop_sleep", "stop_renew"} /\ job = "queued"
    /\ sock' = [sock EXCEPT ![tc] = "stopped"]
    /\ brokerSubs' = {s \in brokerSubs : s[1] # tc}
    /\ \/ /\ sock[tc] \in {"connecting", "up", "closed"}
          /\ queue' = Append(queue, Entry("on_disconnect", tc, 0, TRUE))
       \/ /\ sock[tc] # "closed"
          /\ UNCHANGED queue
    /\ job' = "ran"
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc,
                   nextClient, credExpiry, nextMid, reconnects, now, ghostVars>>

\* The to_thread future completes and wakes the task (mqtt.py:195 _async_connect,
\* mqtt.py:223 _async_subscribe, mqtt.py:235 _async_stop_client).
ThreadReturns ==
    /\ job = "ran"
    /\ job' = "none"
    /\ queue' = Append(queue, Wake(IF pc = "connect_sync" THEN "connect_ret"
                                   ELSE IF pc = "subscribe" THEN "sub_ret"
                                   ELSE "stop_ret"))
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

-----------------------------------------------------------------------------
(* Timers and the credentials provider.                                     *)

\* mqtt.py:185 _async_connect: api.py get_iot_credentials returns fresh credentials.
CredentialsArrive ==
    /\ pc = "await_creds" /\ job = "queued"
    /\ nextClient <= MaxClients
    /\ credExpiry' = [credExpiry EXCEPT ![nextClient] = now + Lifetime]
    /\ nextClient' = nextClient + 1
    /\ job' = "none"
    /\ queue' = Append(queue, Entry("creds", nextClient, 0, TRUE))
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc,
                   sock, nextMid, brokerSubs, reconnects, now, ghostVars>>

\* mqtt.py:185 _async_connect: the provider raises.
CredentialsFail ==
    /\ pc = "await_creds" /\ job = "queued"
    /\ job' = "none"
    /\ queue' = Append(queue, Wake("creds_err"))
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:196 _async_connect: CONNECT_TIMEOUT cancels the wait and the future.
ConnackTimesOut ==
    /\ pc = "await_connack" /\ connack = "pending"
    /\ connack' = "cancelled"
    /\ queue' = Append(queue, Wake("connack"))
    /\ UNCHANGED <<client, connected, lost, pushVars, taskVars, ppc, envVars, ghostVars>>

\* mqtt.py:172-174 _run: the timeout is due renew_margin before the expiry.
RenewalTimerFires ==
    /\ pc = "wait_lost" /\ job = "queued"
    /\ now + Margin >= credExpiry[tc]
    /\ job' = "none"
    /\ queue' = Append(queue, Wake("renew"))
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* mqtt.py:168 _run and mqtt.py:182 _run: asyncio.sleep ends.
SleepEnds ==
    /\ pc = "sleep" /\ job = "queued"
    /\ job' = "none"
    /\ queue' = Append(queue, Wake("sleep_done"))
    /\ UNCHANGED <<connVars, pushVars, pc, tc, batch, jobOk, jobMid, ppc, envVars, ghostVars>>

\* Time passes only while the event loop is idle and _run sits in a long wait:
\* a connect, a stop and a queued callback all take far less than renew_margin.
\* A renewal that is due is handled before more time passes.
Tick ==
    /\ now < MaxTime
    /\ queue = <<>>
    /\ pc \in {"wait_lost", "sleep", "await_creds"}
    /\ ~(pc = "wait_lost" /\ job = "queued" /\ now + Margin >= credExpiry[tc])
    /\ now' = now + 1
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc, queue,
                   nextClient, sock, credExpiry, nextMid, brokerSubs, reconnects, ghostVars>>

-----------------------------------------------------------------------------
(* The broker and paho's network thread.  Callbacks are queued with         *)
(* call_soon_threadsafe: mqtt.py:248 _on_connect, mqtt.py:251 _on_disconnect *)
(* and mqtt.py:255 _on_subscribe.                                           *)

BrokerAccepts(c) ==
    /\ sock[c] = "connecting"
    /\ sock' = [sock EXCEPT ![c] = "up"]
    /\ queue' = Append(queue, Entry("on_connect", c, 0, TRUE))
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc,
                   nextClient, credExpiry, nextMid, brokerSubs, reconnects, now, ghostVars>>

\* paho reports the refused CONNACK and then the closed socket.
BrokerRefuses(c) ==
    /\ sock[c] = "connecting"
    /\ sock' = [sock EXCEPT ![c] = "down"]
    /\ queue' = queue \o <<Entry("on_connect", c, 0, FALSE), Entry("on_disconnect", c, 0, TRUE)>>
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc,
                   nextClient, credExpiry, nextMid, brokerSubs, reconnects, now, ghostVars>>

\* paho closes a lost socket before it calls on_disconnect (_loop_rc_handle in
\* paho's client.py), so client.subscribe can return MQTT_ERR_NO_CONN before
\* the event loop hears of the loss.
ConnectionDrops(c) ==
    /\ sock[c] \in {"connecting", "up"}
    /\ sock' = [sock EXCEPT ![c] = "closed"]
    /\ brokerSubs' = {s \in brokerSubs : s[1] # c}
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc, queue,
                   nextClient, credExpiry, nextMid, reconnects, now, ghostVars>>

\* paho calls on_disconnect for any lost socket, also before the CONNACK.
PahoReportsDisconnect(c) ==
    /\ sock[c] = "closed"
    /\ sock' = [sock EXCEPT ![c] = "down"]
    /\ queue' = Append(queue, Entry("on_disconnect", c, 0, TRUE))
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc,
                   nextClient, credExpiry, nextMid, brokerSubs, reconnects, now, ghostVars>>

\* paho's loop_forever reconnects a lost client by itself with the same signed URL.
PahoReconnects(c) ==
    /\ sock[c] = "down"
    /\ reconnects[c] < MaxPahoReconnects
    /\ now < credExpiry[c]
    /\ sock' = [sock EXCEPT ![c] = "connecting"]
    /\ reconnects' = [reconnects EXCEPT ![c] = @ + 1]
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc, queue,
                   nextClient, credExpiry, nextMid, brokerSubs, now, ghostVars>>

BrokerAnswersSubscribe(c, m, granted) ==
    /\ <<c, m>> \in brokerSubs
    /\ sock[c] = "up"
    /\ brokerSubs' = brokerSubs \ {<<c, m>>}
    /\ queue' = Append(queue, Entry("on_subscribe", c, m, granted))
    /\ UNCHANGED <<connVars, pushVars, taskVars, ppc,
                   nextClient, sock, credExpiry, nextMid, reconnects, now, ghostVars>>

-----------------------------------------------------------------------------

Next ==
    \/ HandleConnect \/ HandleDisconnect \/ HandleSubscribe
    \/ RunCredentials \/ RunCredentialsFailed \/ RunConnectReturned \/ RunConnackArrived
    \/ RunSubscribeReturned \/ RunLost \/ RunRenewalDue \/ RunStopReturned \/ RunSleepOver
    \/ PublishCalled \/ PublishWoken
    \/ ThreadConnects \/ ThreadSubscribes \/ ThreadStops \/ ThreadReturns
    \/ CredentialsArrive \/ CredentialsFail \/ ConnackTimesOut \/ RenewalTimerFires
    \/ SleepEnds \/ Tick
    \/ \E c \in Clients :
           \/ BrokerAccepts(c) \/ BrokerRefuses(c) \/ ConnectionDrops(c)
           \/ PahoReportsDisconnect(c) \/ PahoReconnects(c)
           \/ \E m \in Mids, granted \in BOOLEAN : BrokerAnswersSubscribe(c, m, granted)

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* Properties.  SKILL.md states each one, and the harm of breaking it, in   *)
(* plain words.                                                             *)

TypeOK ==
    /\ client \in 0..MaxClients /\ tc \in 0..MaxClients
    /\ connected \in BOOLEAN /\ lost \in BOOLEAN
    /\ connack \in {"none", "pending", "ok", "refused", "cancelled"}
    /\ pushAvailable \in {"unknown", "yes", "no"} /\ pushDenied \in BOOLEAN
    /\ subscribing \in 0..MaxClients /\ subsSent \in BOOLEAN
    /\ expected \subseteq Mids
    /\ subacks \in [Mids -> {"none", "ok", "failed"}]
    /\ pc \in {"await_creds", "connect_sync", "await_connack", "subscribe", "wait_lost",
               "stop_sleep", "stop_renew", "sleep"}
    /\ batch \in 0..Batches
    /\ job \in {"none", "queued", "ran"} /\ jobOk \in BOOLEAN /\ jobMid \in 0..Batches
    /\ ppc \in {"idle", "waiting", "woken"}
    /\ nextClient \in 1..(MaxClients + 1)
    /\ sock \in [Clients -> {"new", "connecting", "up", "closed", "down", "stopped"}]
    /\ now \in 0..MaxTime
    /\ known \in [Clients -> {"unacked", "live", "dropped"}]
    /\ subClient \in 0..MaxClients
    /\ verdictBy \in {"none", "suback", "drop", "idle_drop", "stale_flag", "stale_client"}
    /\ publishedOn \in {"none", "live", "unacked", "dropped", "expired"}

PublishOnlyAfterConnack == publishedOn # "unacked"

NoPublishOnExpiredCredentials == publishedOn # "expired"

NoPublishOnDroppedConnection == publishedOn # "dropped"

PushVerdictFromCurrentClient == verdictBy # "stale_client"

PushVerdictFromItsOwnSubscription == verdictBy # "stale_flag"

RefusalNeedsUnansweredSubscribe == verdictBy # "idle_drop"

PushDefinite ==
    (/\ client # 0
     /\ subClient = client
     /\ subsSent
     /\ \A m \in expected : subacks[m] # "none")
        => pushAvailable # "unknown"

=============================================================================
