# Skill Taxonomy

ArchMarshal treats skills as dynamic capability nodes. A skill is not just a Markdown instruction file. It is a reproducible unit with metadata, boundaries, triggers, dependencies, and lifecycle state.

## Skill Kinds

```yaml
kind:
  - global_skill
  - functional_skill
  - common_project_skill
  - project_skill
  - generated_project_skill
  - governance_skill
```

## Skill Scopes

```yaml
scope:
  - global
  - functional
  - common_project
  - project
  - module
  - generated
```

## Skill Status Values

```yaml
status:
  - active
  - disabled
  - experimental
  - deprecated
  - archived
```

## Global Skill

Global skills are policy, not toolboxes. They define priority, conflict handling, loading rules, naming rules, and safety boundaries.

Recommended path:

```text
.agents/global/
```

Required properties:

- Highest governance priority.
- Minimal content.
- No project facts.
- No large operational workflows.
- No private project knowledge.

## Functional Skill

Functional skills provide general reusable capabilities that are not tied to a project.

Recommended path:

```text
.agents/skills/functional/
```

Examples:

- Code review.
- Test generation.
- Documentation drafting.
- Architecture analysis.
- Performance analysis.
- Security checking.
- Data analysis.
- File organization.

Required properties:

- Clear tags.
- Clear triggers.
- Clear negative triggers.
- No project-private knowledge.
- Declared external dependencies.
- Scripts and templates stored under the skill path.

## Common Project Skill

Common project skills provide reproducible engineering workflows that are useful across repositories.

Recommended path:

```text
.agents/skills/common-project/
```

Examples:

- Repo audit.
- CI check.
- Release flow.
- Database migration review.
- Monorepo management.
- Package publishing.
- Dependency upgrade.
- Project documentation governance.

Required properties:

- Complete reproducibility.
- Local scripts, templates, references, and optional tests.
- Declared dependencies.
- Default output paths and artifact lifecycles.
- No dependence on undeclared local machine files.

## Project Skill

Project skills are bound to one repository or workspace. They may encode project-specific commands, constraints, deployment flow, or domain details.

Project skills must live under declared project skill paths, usually:

```text
.agents/skills/
```

## Generated Project Skill

Generated project skills are produced from repeated project work. They must not become active hidden behavior. They should be registered, reviewed, and traceable to their source artifacts.

Recommended path:

```text
.agents/skills/generated/
```

## Recommended Tags

```yaml
tags:
  - planning
  - implementation
  - review
  - testing
  - documentation
  - release
  - debugging
  - refactor
  - migration
  - audit
  - frontend
  - backend
  - database
  - api
  - security
  - performance
  - devops
  - ci
  - project-governance
  - context-management
  - artifact-management
  - skill-management
  - reproducibility
```

## Conflict Handling

When two skills appear relevant, the agent should prefer:

1. User's explicit instruction.
2. Global policy.
3. Project `AGENTS.md`.
4. The narrower task-specific skill.
5. The skill with clearer trigger and negative-trigger match.

If a conflict would affect file edits or irreversible decisions, the agent should report the conflict before acting.
