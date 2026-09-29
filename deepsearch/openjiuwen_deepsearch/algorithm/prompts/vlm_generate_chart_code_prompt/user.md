## Required Runtime Style Setup

Include this exact setup block at the beginning of your code:

```python
import seaborn as sns
import os
import matplotlib
from matplotlib import font_manager
matplotlib.use('Agg')
font_manager.fontManager.addfont({{font_path}})
font_name = font_manager.FontProperties(fname=font_path).get_name()

sns.set_style({
    'font.family': font_name,
    'axes.unicode_minus': False,
    'text.color': '#000000',
    'axes.labelcolor': '#000000',
    'axes.titlecolor': '#000000',
    'xtick.color': '#000000',
    'ytick.color': '#000000'
})
custom_palette = [
    "#F1B656", "#397FC7", "#040676", "#808080",
    "#F6631C", "#6D65A3", "#FF9A9B", "#A4E048"
]
sns.set_palette(custom_palette)
```

## Input Data

<chart_title>
{{chart_title}}
</chart_title>

<chart_description>
{{chart_description}}
</chart_description>

<chart_type>
{{chart_type}}
</chart_type>

<chart_data>
{{chart_data}}
</chart_data>

<history_messages>
{{history_messages}}
</history_messages>
