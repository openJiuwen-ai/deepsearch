# # 添加点击事件处理脚本
CLICK_SCRIPT = """
// 节点点击事件处理
network.on("click", function(params) {
    if (params.nodes.length > 0) {
        var nodeId = params.nodes[0];
        var node = nodes.get(nodeId);

        // 如果有url属性，直接跳转
        if (node.url) {
            window.open(node.url, '_blank');
            return false; // 阻止默认行为
        }
    }
});"""

# 图例样式和内容
LEGEND_FORMAT = """
    :root {
        --legend-scale: 1.0;  /* 基础缩放比例，适中尺寸 */
        --base-font-size: 15px;  /* 基础字体大小 */
    }
    
    html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
    }

    .legend {
        position: absolute;
        top: 1.0vh;  /* 使用视口单位 */
        left: 1.0vw;  /* 使用视口单位 */
        background: white;
        padding: calc(0.7vh + 0.3vw);  /* 响应式内边距 */
        border: 1px solid #ccc;
        border-radius: calc(0.4vw + 0.4vh);  /* 响应式圆角 */
        z-index: 1000;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        transform: scale(var(--legend-scale));
        transform-origin: top left;  /* 从左上角开始缩放 */
        width: fit-content;  /* 宽度自适应内容，消除右侧白边 */
        min-width: 120px;  /* 最小宽度，确保legend足够大 */
        max-width: 180px;  /* 最大宽度限制，防止在大窗口时过大 */
    }

    .legend-item {
        display: flex;
        align-items: center;
        margin: calc(0.2vh + 0.2vw) 0;  /* 响应式间距 */
        min-height: 24px;  /* 每个图例项的最小高度 */
    }

    .color-box {
        width: clamp(16px, calc(1.3vw + 1.3vh), 20px);  /* 响应式宽度，限制最大20px */
        height: clamp(16px, calc(1.3vw + 1.3vh), 20px);  /* 响应式高度，限制最大20px */
        border: 1px solid #999;
        margin-right: clamp(6px, calc(0.4vw + 0.4vh), 10px);  /* 响应式边距，限制最大10px */
        border-radius: 50%;
        flex-shrink: 0;  /* 防止缩放时变形 */
    }

    .legend span {
        font-size: clamp(12px, calc(0.8vw + 0.8vh + 6px), 16px);  /* 响应式字体大小，限制最大16px */
        white-space: nowrap;  /* 防止文字换行 */
    }

    /* 响应式媒体查询 */
    @media (max-width: 768px) {
        .legend {
            --legend-scale: 1.0;  /* 中等屏幕时保持较大尺寸 */
            top: 8px;
            left: 8px;
            min-width: 110px;  /* 保持最小宽度 */
            max-width: 160px;  /* 中等屏幕最大宽度 */
            width: fit-content;  /* 宽度自适应内容 */
        }

        .legend span {
            font-size: clamp(12px, calc(0.7vw + 0.7vh + 5px), 16px);  /* 确保最小字体12px */
        }

        .color-box {
            width: clamp(14px, calc(1.1vw + 1.1vh), 20px);  /* 确保最小尺寸14px */
            height: clamp(14px, calc(1.1vw + 1.1vh), 20px);
        }
    }

    @media (max-width: 480px) {
        .legend {
            --legend-scale: 0.9;  /* 小屏幕时稍微缩小但仍保持可见 */
            top: 5px;
            left: 5px;
            padding: 8px;
            min-width: 100px;  /* 保持最小宽度 */
            max-width: 140px;  /* 小屏幕最大宽度 */
            width: fit-content;  /* 宽度自适应内容 */
        }

        .legend span {
            font-size: clamp(11px, calc(0.6vw + 0.6vh + 4px), 15px);  /* 确保最小字体11px */
        }

        .color-box {
            width: clamp(12px, calc(1.0vw + 1.0vh), 18px);  /* 确保最小尺寸12px */
            height: clamp(12px, calc(1.0vw + 1.0vh), 18px);
        }
    }

    @media (min-width: 1200px) {
        /* 大屏幕时限制最大尺寸 */
        .legend {
            --legend-scale: 1.1;  /* 降低缩放比例，防止过大 */
            max-width: 200px;  /* 大屏幕时稍微放宽最大宽度 */
        }
        
        .legend span {
            font-size: clamp(12px, calc(0.8vw + 0.8vh + 6px), 16px);  /* 保持最大16px */
        }
        
        .color-box {
            width: clamp(16px, calc(1.3vw + 1.3vh), 20px);  /* 保持最大20px */
            height: clamp(16px, calc(1.3vw + 1.3vh), 20px);
        }
    }

        #mynetwork {
            width: 100%;
            height: 100vh;
            background-color: #ffffff;
            border: 1px solid lightgray;
            position: relative;
            float: left;
            overflow: hidden;
        }
"""
LEGEND_CONENT = """
<div class="legend">
    <div class="legend-item">
        <div class="color-box" style="background-color: {citation_node_color};"></div>
        <span>{citation_node_name}</span>
    </div>
    <div class="legend-item">
        <div class="color-box" style="background-color: {conclusion_node_color};"></div>
        <span>{conclusion_node_name}</span>
    </div>
    <div class="legend-item">
        <div class="color-box" style="background-color: {intermediate_node_color};"></div>
        <span>{intermediate_node_name}</span>
    </div>
    <div class="legend-item">
        <div class="color-box" style="background-color: {final_conclusion_node_color};"></div>
        <span>{final_conclusion_node_name}</span>
    </div>
</div>
"""