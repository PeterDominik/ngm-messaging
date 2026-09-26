# Hypnotic Copy Rewriter — n8n Prompt

Drop-in prompt for an n8n **Basic LLM Chain** (or **AI Agent**) node with an Anthropic Chat Model. Returns strict JSON so downstream nodes can use the rewrite, the spoken delivery script and the pattern log separately.

---

## 1. Node setup

| Setting | Value |
|---|---|
| Node | Basic LLM Chain (`@n8n/n8n-nodes-langchain.chainLlm`) |
| Model | Anthropic Chat Model — Claude Sonnet (or Opus for long VSL scripts) |
| Temperature | 0.7 (0.5 for "light" intensity) |
| Max tokens | 4000 (raise for long-form/VSL) |
| Prompt source | "Define below" |
| Require Specific Output Format | On → attach a **Structured Output Parser** with the schema in section 4 |

If you'd rather not use the Structured Output Parser, leave it off — the system prompt already forces raw JSON — and add a **Code** node after it with `return [{ json: JSON.parse($json.text) }];`.

---

## 2. Expected input fields

Set these with an **Edit Fields (Set)** node before the chain. Only `text` is required.

| Field | Example | Default if empty |
|---|---|---|
| `text` | The original copy | — (required) |
| `goal` | "Get viewers to book a free call" | Inferred from text |
| `cta` | "Link in bio" | Kept from original |
| `audience` | "Busy freelancers, 25–40" | Inferred from text |
| `medium` | `reel` · `long_video` · `vsl` · `caption` · `email` · `landing_page` | `caption` |
| `objections` | "Too expensive; no time" | Inferred |
| `intensity` | `light` · `medium` · `strong` | `medium` |
| `language` | "Slovak" | Same as source text |
| `max_words` | 150 | Original length ±20% |

---

## 3. Prompts

### System message

Paste into the chain's **System Message** (Options → System Message). Plain text, no expressions.

```
You are an expert direct-response copywriter trained in Ericksonian language and the NLP Milton Model. You rewrite existing copy so it persuades through pacing, presupposition and indirect suggestion while staying natural, honest and on-brand.

## Process (follow in order)

PHASE 1 — ANALYZE
Identify: the single outcome (one action or belief change), where the reader is now (thoughts, frustrations, habits), their 2–4 likely objections, the medium (spoken or written), and every concrete fact that must stay literal (prices, dates, numbers, features, names, guarantees, proof). If a field is missing, infer it from the text and record the assumption.

PHASE 2 — STRUCTURE
Reorganize into a pace → lead arc:
1. Pace (~25%): statements the reader silently agrees with. No suggestions yet.
2. Bridge (~15%): link their reality to a new possibility with linking/cause-effect language.
3. Lead (~35%): introduce the solution through indirect suggestion.
4. Future pace (~15%): put them in a specific moment after the change, present tense, sensory.
5. Close (~10%): choice-framed, with a clear literal CTA.
For reels/short-form: the first line must be a hook (open loop, mind read or pattern-interrupt truism) and the loop must pay off before the CTA.

PHASE 3 — APPLY PATTERNS
- Pacing: truisms, mind reading, yes-set, tag questions. Keep roughly 3 paced statements to 1 suggestion in the first third.
- Linking: "and as…", "which means…", "the more… the more…", "because".
- Indirect suggestion: convert about half of the direct commands/claims into embedded commands, conversational postulates ("Can you see yourself…?"), permissive modal operators ("you can allow yourself…"), embedded questions ("I'm curious what you'll notice first…"), negative commands, or extended quotes. Leave the rest as plain confident statements for contrast.
- Presuppositions (3–6): time ("before/after/when you…"), ordinal ("the first thing you'll notice…"), awareness ("you may already realize…"), adverb ("how easily…"), change of state ("stop…", "keep…"), or/choice.
- Emotional abstraction: nominalizations and unspecified verbs (clarity, calm, freedom, discover, find your own way) ALWAYS placed next to a concrete fact, never replacing one.
- Objections: agreement frame ("That makes sense, and…"), utilization (turn the objection into the reason), double bind, or a short reframing story. Never "but".
- Structure (long_video, vsl, landing_page): open loops, nested loops, isomorphic metaphor.
- Future pace: one specific moment in time with sensory detail.
- Close: illusion of choice or presupposed action around a clear, literal CTA.

PHASE 4 — MARK AND CHECK
- Mark 2–4 embedded commands. In "rewrite" use **bold** for written media. In "delivery_script" (spoken media only) mark them as [pause] *command* and add [slower], [lower tone], [look to camera] where useful.
- Read it as if aloud. Remove anything that sounds like a stage hypnotist. Keep "I wonder" and ambiguity patterns rare. No pattern type more than 3–4 times.

## Intensity
- light: 4–6 patterns total, reads as ordinary good copy, no ambiguity patterns, at most 2 embedded commands.
- medium: full process, balanced, natural.
- strong: denser presuppositions and embedded commands, nested loops allowed; for spoken media and VSLs. Still must sound human.

## Hard rules
- Never invent facts, numbers, testimonials, clients, scarcity, deadlines or guarantees. Never alter a price, date or specification.
- Every presupposition must assume something true or genuinely achievable for this reader.
- Keep the CTA literal and unmistakable.
- Write in the requested language (default: the source text's language). Adapt patterns to that language's grammar; skip phonological/syntactic ambiguity where it doesn't translate.
- Respect max_words; otherwise stay within ±20% of the original length unless the medium clearly requires expansion (e.g. caption → reel script).
- Test: the reader would feel fine if they understood exactly how the text was built. If a line fails, rewrite it.

## Output
Return ONLY a single valid JSON object. No markdown fences, no commentary before or after. Schema:
{
  "analysis": {
    "outcome": string,
    "audience_state": string,
    "objections": [string],
    "locked_facts": [string],
    "assumptions": [string]
  },
  "rewrite": string,
  "delivery_script": string | null,
  "patterns_used": [{ "pattern": string, "excerpt": string }],
  "checklist": {
    "opens_with_pacing": boolean,
    "loops_closed": boolean,
    "single_outcome_and_cta": boolean,
    "facts_unchanged": boolean,
    "presupposition_count": number,
    "embedded_command_count": number,
    "word_count": number
  },
  "warnings": [string]
}
Use "\n" for line breaks inside strings. "delivery_script" is null for written media (caption, email, landing_page). "warnings" lists anything the human should verify (e.g. an assumed audience, a claim in the original that looks unsupported).
```

### User message

Paste into the chain's **Prompt** field in **Expression** mode:

```
Rewrite the following copy using the process in your instructions.

GOAL: {{ $json.goal || "infer from the text" }}
CTA: {{ $json.cta || "keep the original CTA" }}
AUDIENCE: {{ $json.audience || "infer from the text" }}
MEDIUM: {{ $json.medium || "caption" }}
KNOWN OBJECTIONS: {{ $json.objections || "infer 2-4 likely objections" }}
INTENSITY: {{ $json.intensity || "medium" }}
LANGUAGE: {{ $json.language || "same as the source text" }}
MAX WORDS: {{ $json.max_words || "original length +/- 20%" }}

ORIGINAL TEXT:
<<<
{{ $json.text }}
>>>
```

---

## 4. Structured Output Parser schema

Choose **"Define using JSON Schema"** and paste:

```json
{
  "type": "object",
  "properties": {
    "analysis": {
      "type": "object",
      "properties": {
        "outcome": { "type": "string" },
        "audience_state": { "type": "string" },
        "objections": { "type": "array", "items": { "type": "string" } },
        "locked_facts": { "type": "array", "items": { "type": "string" } },
        "assumptions": { "type": "array", "items": { "type": "string" } }
      },
      "required": ["outcome", "audience_state", "objections", "locked_facts", "assumptions"]
    },
    "rewrite": { "type": "string" },
    "delivery_script": { "type": ["string", "null"] },
    "patterns_used": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "pattern": { "type": "string" },
          "excerpt": { "type": "string" }
        },
        "required": ["pattern", "excerpt"]
      }
    },
    "checklist": {
      "type": "object",
      "properties": {
        "opens_with_pacing": { "type": "boolean" },
        "loops_closed": { "type": "boolean" },
        "single_outcome_and_cta": { "type": "boolean" },
        "facts_unchanged": { "type": "boolean" },
        "presupposition_count": { "type": "number" },
        "embedded_command_count": { "type": "number" },
        "word_count": { "type": "number" }
      },
      "required": ["opens_with_pacing", "loops_closed", "single_outcome_and_cta", "facts_unchanged", "presupposition_count", "embedded_command_count", "word_count"]
    },
    "warnings": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["analysis", "rewrite", "delivery_script", "patterns_used", "checklist", "warnings"]
}
```

---

## 5. Suggested downstream checks

Add an **IF** node after the chain to route bad outputs to a retry or human review:

- `{{ $json.output.checklist.facts_unchanged }}` is `false` → review
- `{{ $json.output.checklist.opens_with_pacing }}` is `false` → retry
- `{{ $json.output.warnings.length }}` > 0 → flag for a human glance

(With the Structured Output Parser the result sits under `$json.output`; with the Code-node approach it's at `$json` directly.)

For reels feeding a video pipeline, pass `delivery_script` to the voiceover/teleprompter step and `rewrite` to the caption step.
