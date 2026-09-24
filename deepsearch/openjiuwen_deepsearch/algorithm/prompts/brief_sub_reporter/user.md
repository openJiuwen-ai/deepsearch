# Authoritative Writing Context

Use the overall outline, the current top-level section, and the current chapter outline as authoritative constraints
for this brief chapter. The current chapter outline is the primary writing boundary.

<overall_outline>
{{ outline }}
</overall_outline>

<current_section>
title: {{ current_section }}
description: {{ current_section_description }}
format_requirements: {{ current_section_format_requirements }}
</current_section>

<current_chapter_outline>
{{ current_chapter_outline }}
</current_chapter_outline>

{% if section_material_bindings_text %}
<section_material_use_contract>
The following user-material assignments apply only to this chapter. A material may be reused by another chapter, but
do not infer its role or claims there. Use a material only for its assigned role and allowed claims; do not broaden a
listed claim or treat the binding itself as evidence for facts absent from the cited material.
{{ section_material_bindings_text }}
</section_material_use_contract>
{% endif %}
{% if has_exclusion %}
<excluded_sources>
{{ exclusion_instruction }}
</excluded_sources>
Honor these excluded-source constraints when selecting evidence and citations.
{% endif %}

{% if audience_role or tone %}
## Report Detail Constraints
{% if audience_role %}
- **Target Audience**: {{ audience_role }}. Keep the chapter directly actionable for this audience.
{% endif %}
{% if tone %}
- **Tone Intent**: {{ tone }}. Keep argument posture and wording consistent with this style.
{% endif %}
{% endif %}

Collected Information:
{{ collected_information }}

{% if writing_guidance %}
Internal Writing Guidance (editorial only; not evidence):
{{ writing_guidance }}
{% endif %}

Output language must be **{{ language }}**.
