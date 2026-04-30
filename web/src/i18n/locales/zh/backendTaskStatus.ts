import type { TranslationTree } from '../../types'

const zh: TranslationTree = {
  backendTaskStatus: {
    task_failed_detail: '任务失败：{{detail}}',
    route_failed_detail: '路由失败：{{detail}}',
    reuse_studio_leader_plan: '沿用来源工作室「{{name}}」，Leader 开始拆解任务…',
    agent_zero_evaluating: '0 号 Agent 正在评估任务域…',
    agent_zero_direct: '0 号正在直接处理…',
    answer_done: '回答完毕',
    creating_new_studio: '0 号拟建新事业部…',
    handed_to_studio_leader: '已交至「{{name}}」，Leader 开始拆解任务…',
    leader_need_clarification: 'Leader 需要确认一些细节，请前往任务页填写问卷',
    await_plan_review: '等待用户审查 Leader 的拆解方案…',
    clarify_received_replanning: '已收到用户澄清，Leader 重新规划中…',
    rerun_saved_plan: '正在沿用原方案重新执行…',
    leader_dispatching: 'Leader 已开始分发步骤并推进执行…',
    executing_step: '正在执行「{{label}}」…',
    agent0_replan_iteration: 'Agent0 正在根据新的迭代要求重新规划…',
    agent0_iterate_from_result: 'Agent0 正在根据结果对话继续规划迭代…',
    scheduled_failed_detail: '定时任务执行失败：{{detail}}',
    task_summary_done: '任务已完成汇总',
    task_summary_done_with_blockers: '任务已完成汇总，但部分步骤存在阻塞',
    recovery_terminated: '服务重启前任务仍在运行，执行上下文已丢失，已自动终止。',
    retry_running: '正在为「{{label}}」重新执行…',
    retry_success: '「{{label}}」重试成功！',
    retry_still_blocked: '「{{label}}」仍然受阻：{{detail}}',
    edit_manual_ok: '「{{label}}」已手动改写成功。',
    cascade_deps_incomplete_skipped: '「{{label}}」前置依赖未完成，已跳过。',
    cascade_deps_incomplete_deferred: '「{{label}}」前置依赖未完成，暂缓重试。',
    cascade_running_resume: '正在恢复执行「{{label}}」…',
    cascade_running_rerun: '正在重新执行「{{label}}」…',
    cascade_step_ok: '「{{label}}」执行成功！',
    cascade_step_blocked: '「{{label}}」受阻：{{detail}}',
  },
}

export default zh
