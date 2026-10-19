# Owner-DM command classification · fixed rule set

> Purpose: the **complete context** for the routing-layer explicit-command
> classifier. Design rule: explicit-command judgement at the routing layer
> is the exception — the fixed rules *are* the complete context, no
> conversation history needed. A small amount of intelligence may do
> closed-loop classification with fixed labels; low confidence goes
> straight to default. It outputs only the classification — no execution,
> no content-layer judgement, no clarifying questions.
> Open-ended understanding and answering must live in a side chat or the
> main chat, where context exists.

## 1. Scope

- Only the owner's private DM (convoId `<ownerDm>` from `bridge.config.json`).
  Groups and other DMs are out of scope.
- The classifier handles **regular message entries** (entries already
  consumed by the detector, entries already tagged `directMain` are not
  re-classified, to avoid double handling).
- Two stages: `detector.py` regexes keep handling known variants
  (deterministic fast path); this classifier handles **new variants** the
  regexes miss, plus **create/wake task-chat commands** the regexes never
  covered (flexibility supplement).

## 2. Classification labels (fixed, pick one)

### 2.1 Create command
Explicit command to create a new task side chat. Naming convention:
`WA: <ownerLabel> · <task name>`, task name a short 2–6 word label.
- Example: "open a new chat to track the Pinduoduo order"
- Output param: `task=<task name>`
- Key test: must show clear create/new intent + an extractable task name;
  "make a chat" with no task name → low confidence → not a command.

### 2.2 Wake command
Explicit command to switch routing to a target. Targets (pick one):

| target | meaning | examples (non-exhaustive; classifier may generalize) |
|---|---|---|
| `main` | switch to main-chat direct line | whatever phrases you put in `triggers.toMain` |
| `sidechat` | switch back to the DM side chat | whatever phrases you put in `triggers.toSidechat` |
| `voice` / `text` | reply form | whatever phrases you put in `triggers.toVoice` / `triggers.toText` |
| `task:<name>` | wake an existing task chat | "switch to <task name>", "open the <task name> chat" |

- Scope rules (the classifier must obey):
  - Trigger wrapped in brackets/quotes, or content introduced by a colon →
    **one-shot**: affects only this message, the saved default is untouched.
  - Trigger containing an explicit "switch"-family word → **always switches
    the default** (overrides the one-shot reading).
  - Bare trigger, or trigger directly followed by content with no colon →
    **switches the default**.
  - Every default switch must be followed by a **text confirmation** stating
    the new default (the confirmation itself is always text).
- Key test: the command intent should be the message's main intent
  (usually at the start); a trigger word mentioned in passing mid-message
  is not a command → not a command.

### 2.3 Not a command (→ default)
Neither of the above → follow the current default route (owner DMs default
to `WA: <ownerLabel>`, handled by the existing template).
**Low confidence always lands here** — better to miss than to misfire.

## 3. Classifier contract

- Input: one message body + this rule file (complete context). **No**
  conversation history needed.
- Output (classification only, no execution):
  `label=create|wake|not-a-command; target=…; params=…; confidence=high/medium/low; evidence=<short phrase from the message>`
- Forbidden: executing routing actions, interpreting message content,
  asking clarifying questions.
- Example outputs:
  - `label=wake; target=main; params=rest="check my Bland balance"; confidence=high; evidence="please switch to main"`
  - `label=not-a-command; confidence=high; evidence="no explicit command intent"`

## 4. Action table (deterministic; the worker calls `router-act.py` per label, no judgement)

| label / target | action |
|---|---|
| wake, target=main | ① `router-act.py switch-main` writes channel-mode.json; ② if trailing content: `router-act.py requeue-main --body "<content>"` re-queues as a directMain entry (the queue-wake hook delivers it to main); ③ send a system confirmation via send.py |
| wake, target=sidechat | `router-act.py switch-sidechat` + system confirmation (note: the DM wake hook only fires in sidechat mode, so this branch is mostly for completeness) |
| wake, target=voice/text | ① `router-act.py replymode voice\|text` writes reply-mode.json; ② a default switch must be confirmed in text (stating the new default); a one-shot affects only this message, no confirmation needed; ③ trailing content (if any) is handled normally per the existing template |
| wake, target=task:\<name\> | the worker has no cross-chat delivery mechanism → state the classification result in the reply and hand it to the main/agent layer; record honestly in the run summary |
| create | the worker has no chat-creation mechanism → state the classification result (including the task name) in the reply and hand it to the main/agent layer; record honestly in the run summary |
| not-a-command | default: existing template (paste the body verbatim; `[reply]` gets a proper reply via send.py; `[log-only]` stops after pasting) |

## 5. Honesty requirements

- A classification must have evidence: `evidence` must quote a short phrase
  from the message, never invented.
- A routing action may only be claimed complete with proof (file written /
  send.py returned ok / queue entry enqueued). Steps not actually finished
  must never be reported as "done".
