# Dependency Outline Format Requirements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dependency-driven outlining and writing preserve structured section format requirements and local section contracts without changing the normal outline production path.

**Architecture:** Extend only `creat_dep_driving_outline_tool()` and the two dependency prompts, then bridge the already-produced section fields across the dependency writing workflow boundary. Preserve all Pydantic defaults and normal Outliner code; protect that boundary with targeted dependency tests plus normal-path regression tests.

**Tech Stack:** Python 3.12, Pydantic, openJiuwen workflows, Jinja prompt templates, pytest, pytest-asyncio, uv.

---

### Task 1: Lock the dependency tool contract with failing tests

**Files:**
- Modify: `tests/algorithm/query_understanding/test_dependency_outliner.py`
- Modify later: `openjiuwen_deepsearch/algorithm/query_understanding/outliner.py:271-365`

- [ ] **Step 1: Add schema and required-field tests**

Add tests that obtain the section item schema from `creat_dep_driving_outline_tool(5)` and assert:

```python
properties = tool.card.input_params["properties"]["sections"]["items"]["properties"]
required = tool.card.input_params["properties"]["sections"]["items"]["required"]

assert properties["format_requirements"]["type"] == "array"
assert properties["format_requirements"]["items"]["type"] == "string"
assert properties["section_focus"]["minLength"] == 1
assert properties["focus_dimensions"]["minItems"] == 1
assert {
    "format_requirements",
    "section_focus",
    "focus_dimensions",
    "id",
    "parent_ids",
    "relationships",
}.issubset(required)
```

Compare only the three shared field schemas with `create_outline_tool(5)` to detect later drift without modifying the normal tool.

- [ ] **Step 2: Add dependency tool-call validation tests**

Import `check_tool_call`, `generate_outline`, and `CustomValueException`. Build a valid dependency section containing all fields. Parameterize removal of `format_requirements`, `section_focus`, and `focus_dimensions`; each call must raise `CustomValueException` containing the missing field. Add successful cases for `format_requirements=[]` and for preservation of ordered non-empty requirements through `generate_outline()`.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding\test_dependency_outliner.py -q
```

Expected: new tests fail because the dependency schema lacks `format_requirements`, does not require the shared contract, and lacks `minLength`/`minItems`.

- [ ] **Step 4: Implement the minimal dependency tool schema change**

In `creat_dep_driving_outline_tool()` only:

```python
"sections": {
    "type": "array",
    "description": _section_list_description(section_num),
    "items": {
        "type": "object",
        "properties": {
            # existing title and description
            "format_requirements": {
                "type": "array",
                "description": _format_requirements_description(),
                "items": {"type": "string"},
            },
            # existing dependency fields
            "section_focus": {
                "type": "string",
                "minLength": 1,
                # retain existing description
            },
            "focus_dimensions": {
                "type": "array",
                "minItems": 1,
                # retain existing description and items
            },
        },
        "required": [
            "title",
            "description",
            "format_requirements",
            "id",
            "parent_ids",
            "relationships",
            "section_focus",
            "focus_dimensions",
        ],
    },
}
```

Call `_section_description_description("and its relationships")` without disabling format guidance. Do not edit `create_outline_tool()` or `_has_required_section_value()`.

- [ ] **Step 5: Run dependency and normal Outliner tests and verify GREEN**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding\test_dependency_outliner.py tests\algorithm\query_understanding\test_outliner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the tool-contract change**

```powershell
git add openjiuwen_deepsearch/algorithm/query_understanding/outliner.py tests/algorithm/query_understanding/test_dependency_outliner.py
git commit -m "fix: align dependency outline section contract"
```

### Task 2: Make dependency prompts preserve structured format requirements

**Files:**
- Modify: `tests/algorithm/query_understanding/test_dependency_outliner.py`
- Modify: `openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner.md`
- Modify: `openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner_interaction.md`

- [ ] **Step 1: Add prompt-rendering tests**

Use `apply_system_prompt()` with the minimum context already used by existing dependency prompt tests. Render both prompt names and assert each output contains the literal contract concepts:

```python
assert "format_requirements" in rendered
assert "exact column" in rendered
assert "required row" in rendered
assert "item-by-item" in rendered
assert "source" in rendered
assert "[]" in rendered
```

Also assert the prompts distinguish substantive research scope in `description` from output constraints in `format_requirements`, and that the interaction prompt instructs user feedback to update the structured field.

- [ ] **Step 2: Run the prompt tests and verify RED**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding\test_dependency_outliner.py -q
```

Expected: prompt assertions fail because neither dependency prompt mentions `format_requirements`.

- [ ] **Step 3: Add the minimal prompt contract**

Add a focused section to both prompts that says:

```text
- Put substantive scope, entities, time ranges, questions, dimensions, and dependency relationships in description.
- Put Markdown tables, exact column names/order, required rows, item-by-item enumeration, length/style rules, source restrictions, and deliverable rules in format_requirements.
- Preserve user-provided labels and ordering exactly.
- Use [] when no section-specific format requirement exists; never omit the field.
- Do not duplicate the same format constraint in description.
- Every section must have a non-empty section_focus and at least one focus_dimensions item.
```

In the interaction prompt, add that format-related feedback updates `format_requirements`, not only `description`.

- [ ] **Step 4: Run prompt and normal Outliner regression tests and verify GREEN**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding\test_dependency_outliner.py tests\algorithm\query_understanding\test_outliner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the prompt change**

```powershell
git add openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner.md openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner_interaction.md tests/algorithm/query_understanding/test_dependency_outliner.py
git commit -m "fix: preserve dependency outline format requirements"
```

### Task 3: Bridge dependency writing workflow fields

**Files:**
- Modify: `tests/workflow/test_dependency_writing_nodes.py`
- Modify: `tests/workflow/test_dependency_workflow.py`
- Modify: `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_writing_team_nodes.py`

- [ ] **Step 1: Add start-node propagation tests**

Extend `test_section_writing_start_node_init` inputs with:

```python
"section_format_requirements": [
    "Use a Markdown table",
    "Columns: Product, Price, Risk",
],
"section_local_contract": {
    "section_focus": "product_comparison",
    "allowed_dimensions": ["price", "risk"],
    "is_final_decision_section": False,
},
```

Assert the dumped `section_context` preserves both objects exactly. Add a separate test asserting missing fields produce `[]` and `{}`.

- [ ] **Step 2: Add workflow input-mapping test**

Inspect the start component registered by `build_dependency_writing_workflow()` using the same internal graph pattern already present in workflow tests. Assert its input schema or mapping includes `section_format_requirements` and `section_local_contract`.

- [ ] **Step 3: Run workflow tests and verify RED**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\workflow\test_dependency_writing_nodes.py tests\workflow\test_dependency_workflow.py -q
```

Expected: propagation and mapping assertions fail because the dependency start node ignores both fields.

- [ ] **Step 4: Implement minimal field propagation**

In `SectionWritingStartNode.invoke()` add:

```python
section_format_requirements=inputs.get("section_format_requirements", []),
section_local_contract=inputs.get("section_local_contract") or {},
```

In the workflow start inputs schema add:

```python
"section_format_requirements": "${section_format_requirements}",
"section_local_contract": "${section_local_contract}",
```

- [ ] **Step 5: Run workflow tests and verify GREEN**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\workflow\test_dependency_writing_nodes.py tests\workflow\test_dependency_workflow.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the workflow change**

```powershell
git add openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_writing_team_nodes.py tests/workflow/test_dependency_writing_nodes.py tests/workflow/test_dependency_workflow.py
git commit -m "fix: propagate dependency section contracts"
```

### Task 4: Verify Reporter consumption and document the contract

**Files:**
- Modify if coverage is missing: `tests/report/test_sub_report.py`
- Modify: `docs/feature/algorithm/query-understanding.md`
- Modify: `docs/feature/algorithm/report-generation/sub-report-generation.md`

- [ ] **Step 1: Inspect existing Reporter prompt coverage**

Locate the test that captures `apply_system_prompt()` or the LLM input from sub-report generation. If it already asserts exact `section_format_requirements` and section-local contract content, retain it unchanged. Otherwise add one test using real Reporter prompt construction and assert exact column order, source restriction, section focus, allowed dimensions, and non-final-decision restriction appear.

- [ ] **Step 2: If a Reporter assertion was added, verify RED then GREEN without production changes**

Run the exact new test before workflow implementation to confirm the missing boundary makes it fail, then rerun after Task 3. Expected final result: PASS using existing Reporter production code. Do not edit Reporter implementation unless the test proves an additional defect.

- [ ] **Step 3: Update feature documentation**

In `query-understanding.md`, document that new dependency outlines require structured `format_requirements`, non-empty `section_focus`, and non-empty `focus_dimensions`, while legacy model defaults remain loadable.

In `sub-report-generation.md`, document that both writing modes receive `section_format_requirements` and `section_local_contract`, including the dependency workflow boundary.

- [ ] **Step 4: Run targeted regression tests**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding\test_dependency_outliner.py tests\algorithm\query_understanding\test_outliner.py tests\workflow\test_dependency_writing_nodes.py tests\workflow\test_dependency_workflow.py tests\report\test_sub_report.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit tests and documentation**

```powershell
git add tests/report/test_sub_report.py docs/feature/algorithm/query-understanding.md docs/feature/algorithm/report-generation/sub-report-generation.md
git commit -m "docs: describe dependency section contracts"
```

### Task 5: Final verification

**Files:**
- Verify all modified files

- [ ] **Step 1: Run relevant module regression**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest tests\algorithm\query_understanding tests\workflow tests\report -m "not llm" -q
```

Expected: zero failures.

- [ ] **Step 2: Run the full non-LLM suite**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run pytest -m "not llm"
```

Expected: zero failures.

- [ ] **Step 3: Run compilation and diff checks**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\uv.exe" run python -m compileall -q openjiuwen_deepsearch server
git diff --check
git status --short
```

Expected: compilation and diff checks exit 0; status lists only intentional implementation files and pre-existing user artifacts remain untouched.

- [ ] **Step 4: Review normal-path isolation**

Confirm `git diff` does not modify `create_outline_tool()`, `_has_required_section_value()`, `outliner.md`, `outliner_template.md`, normal editor workflow files, or Pydantic model defaults.

- [ ] **Step 5: Commit any final test-only correction**

If verification required a test or documentation correction, commit only that correction with a focused message. Do not bundle user artifacts or the externally moved design document deletion.
