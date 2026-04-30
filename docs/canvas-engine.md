# AStudio - 画布渲染引擎 (Canvas Engine)

## 设计思想
在原型实现中，不仅将大模型 (LLMs) 当作对话接口，而是通过有向无环图 (DAG) 或树状路径在 **空间上** 展开其解决问题的行为流程。

Canvas Engine 是前端最核心的模块，采用 **React Flow** 构建实时交互图。

## 架构

主要分为三个技术栈支持点：
1. **React Flow**：底层负责图的渲染、拖拽、事件监听及 MiniMap。
2. **Server-Sent Events (SSE)**：长连接。后端在执行 `agent_zero` 到 `sub_agent` 级联生成时，会实时不断吐出节点生成状态。
3. **Zustand (CanvasStore)**：前端状态切片存储管理。用来接收 SSE 流转换过来的 action（`addNode`, `appendStreamChunk`, `updateNodeSummary` 等），并直接更新 React Flow 对应节点。

## CanvasStore State Model

*   **`nodes`**: Graph 节点的数组（包含各种自定义数据如 `agentRole`, `status`, `distilledSummary`）
*   **`edges`**: Graph 连接线。在 Prototype 中使用了 `smoothstep` 类型并配以不同的动画提示（比如 `main` 链路默认有流光流动，`correction` 发散支路会换不同高亮色）。
*   **`streamBuffers`**: 为每个当前正在 running 状态的节点暂存其实时输出内容的文字字符，达到打字机回显功能。
*   **`selectedNodeId`**: 选中态同步。

## Component 交互

*   **PathNode**: 用于替换 ReactFlow 默认节点的自定义组件。根据 `status` 变色。包含一个脉冲(pulse)徽章在 running 时发光提示。并显示文字截断（若节点完结，显示 `distilledSummary` 截取的文字段）。
*   **NodePanel**: 画布侧边栏（右侧划出）。这提供了一个深度的洞察视角。
    *   显示完整长文本（解决卡片空间局限）。
    *   提供基于历史 **追问 (Deep Dive)** 记录功能模块。
    *   底部的操作区预留了【重置节点】与【生成分支】功能入口。

## 布局引擎计划
目前原型由于串联节点直接将每个步骤 Y 轴 +150 固定偏移实现，但日后将拓展为非线性或自顶向下结构。下沉期功能将引入 `dagre` 算法自动根据树层级重算。
