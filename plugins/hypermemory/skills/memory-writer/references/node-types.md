# HyperMemory node types and data envelopes

Read this reference only after a candidate passes the durability gate and a new
node or material structured-data update is justified.

## Key convention

Use lowercase snake-case keys shaped as `{node_type}_{specific_name}`.

Supported types and prefixes:

```text
user_          user
person_        person
organization_  organization
component_     component
event_         event
decision_      decision
concept_       concept
artifact_      artifact
project_       project
technology_    technology
preference_    preference
fact_          fact
skill_         skill
```

`user_profile` remains the singleton exception.

Do not invent abbreviations such as `style_`, `org_`, or `tech_` for new nodes.
Treat writing and visual style rules as `preference` nodes with a
`preference_...` key. Preserve and update an existing legacy key rather than
creating a duplicate solely to correct its prefix.

Keys identify durable entities, not turns. Add a date only when time is part of
the identity, such as a specific event or a versioned decision that must remain
separate from its successor.

Keys are permanent, so name the subject, never its status: no `_proposed`,
`_pending`, `_recommended`, `_awaiting`, `_draft` or `_wip`. Write
`decision_usage_summary_index_read`, not
`decision_usage_query_rewrite_proposed`; the status lives in `data.status`.

## Shared data rules

- Omit fields that do not apply.
- Preserve useful existing fields during updates. `hm_update` replaces a
  node's whole `data` object, so send the full hydrated `data` with your
  changes.
- Use ISO dates when an exact date is known; do not invent precision.
- Put uncertainty in the data rather than obscuring it in prose.
- Keep arrays specific and deduplicated.
- Do not store a second narrative summary inside `data`.
- Commit hashes, image digests, record or user IDs, file paths, hostnames and
  ports belong in `data`, never in the description.
- Status that will change (proposed, pending, approved, deployed, declined,
  superseded) belongs in `data.status`.
- A correction records in `data` what was corrected and how it was verified.

## Decision

Use for a committed choice, not a proposal still under discussion.

```json
{
  "chosen": "selected approach",
  "rejected": ["material alternatives"],
  "rationale": "why the choice was made",
  "date": "ISO date or known period",
  "reversibility": "low | medium | high",
  "scope": "what the decision governs",
  "status": "current | superseded",
  "open_conflict": "question that settles a possible conflict with a standing preference"
}
```

Set `open_conflict` only while a possible conflict with a standing preference
is unresolved, and link the decision to that preference with a relationship
that names the conflict.

## Event

Use only for a material occurrence with future historical or diagnostic value.

```json
{
  "date": "ISO date or known period",
  "participants": ["people, projects, or systems"],
  "trigger": "what caused the event",
  "outcome": "material result",
  "impact": "lasting consequence"
}
```

Rules, decisions, risks and lessons the event produced are their own
`preference`, `decision` and `fact` nodes linked to it. Never list them in the
event's `data`, and never add a `lesson` field.

## Concept

```json
{
  "domain": "field or subject",
  "key_attributes": ["defining characteristics"],
  "distinctions": "how it differs from adjacent concepts",
  "scope": "where the concept applies"
}
```

## Person

```json
{
  "role": "primary role or title",
  "organization": "affiliation",
  "expertise": ["knowledge domains"],
  "relationship_to_user": "why this person matters to the user"
}
```

## Organization

```json
{
  "kind": "company | firm | public body | community | other",
  "purpose": "what the organisation does",
  "relationship_to_user": "why it matters",
  "jurisdictions": ["known jurisdictions"]
}
```

## Project

```json
{
  "goals": ["durable project goals"],
  "constraints": ["lasting requirements or limits"],
  "status": "current state",
  "repository": "durable path or URL when useful",
  "owners": ["responsible people or organisations"]
}
```

## Component

```json
{
  "purpose": "role in the wider system",
  "boundaries": ["what it owns or excludes"],
  "interfaces": ["important connections"],
  "status": "planned | active | retired"
}
```

## Technology

```json
{
  "purpose": "role in the system",
  "deployment": "how and where it is used",
  "version_or_constraint": "version or durable limit when known",
  "alternatives_considered": ["material alternatives"]
}
```

## Preference

Use for stable instructions that should shape later work, including writing and
visual style.

```json
{
  "scope": "brand, project, audience, or work type",
  "facet": "voice | tone | lexicon | format | visual | workflow | other",
  "strength": "mandatory | preferred | situational",
  "intent": "why the preference exists",
  "rules": ["observable do-rules"],
  "avoid": ["explicit anti-patterns"],
  "examples": [
    {
      "do": "preferred example",
      "dont": "example to avoid"
    }
  ]
}
```

Translate feedback into operational rules. Avoid storing a complaint or raw
quote when the durable value is the instruction it implies. A preference the
user stated links to `user_profile`.

## Fact

```json
{
  "source": "where the fact came from",
  "confidence": "high | medium | low",
  "date_learned": "ISO date or known period",
  "scope": "what the fact applies to",
  "valid_as_of": "date when time-sensitive"
}
```

## Artifact

Store only a canonical artifact likely to be referenced again, not every output
or intermediate revision.

```json
{
  "artifact_type": "document | code | image | recording | dataset | workbook",
  "location": "durable path, URL, or reference",
  "status": "current | superseded | draft",
  "purpose": "why the artifact exists",
  "produced_by": "decision, process, or owner",
  "version": "durable version when relevant"
}
```

## Skill

Use for a durable, named capability or governed workflow, not a one-off action.

```json
{
  "purpose": "outcome the skill produces",
  "inputs": ["required input classes"],
  "outputs": ["expected output classes"],
  "constraints": ["durable operating boundaries"],
  "owner": "person, product, or organisation"
}
```

## User

Use `user_profile` for stable cross-project facts and preferences about the
primary user. Prefer a project-scoped preference node when an instruction does
not apply globally.

```json
{
  "roles": ["durable roles"],
  "working_preferences": ["cross-project preferences"],
  "locations_or_timezones": ["useful durable context"]
}
```
