**IMPORTANT RULES FOR DISCOVERED_CLUES:**
- `discovered_clues` is REPLACED (not appended) by whatever you provide in the state patch
- If you include `discovered_clues` in the state patch, you MUST retain all information in the previously discovered clues unless you discover them to be incorrect or no longer relevant.
- Discovered clues should restrict the candidate pool of a variable by either eliminating specific candidates or by eliminating groups of candidates based on a property.
- If a candidate is changed, make sure all discovered clues that are related to that candidate are also updated.
- Format: "ELIMINATES: [candidate/group description] - [reasoning/evidence]. [If applicable: Include any assumptions about other variables that cause a contradiction or lead to this elimination] | Sources: [URL1, URL2, ...]

Examples of GOOD evidence:
    * "ELIMINATES: John Smith - published work in 1823, but University College London was not established until 1826. Assumption: Variable 2 (university) is University College London| Sources: url_of_the_source
    * "ELIMINATES: Chicago - as there is no soccer team that is based in Chicago that competed in the FA Cup (requirement stated in question clues) | Sources: url_of_the_source1, ...
    * "ELIMINATES:  Ned Stark - he dies in Season 1 (requirement stated in question clues). Assumption: Variable 4 (director) is David Benioff and Variable 6 (tv show) is Game of Thrones| Sources: url_of_the_source
