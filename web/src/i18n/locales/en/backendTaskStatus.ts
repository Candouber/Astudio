import type { TranslationTree } from '../../types'

const en: TranslationTree = {
  backendTaskStatus: {
    task_failed_detail: 'Task failed: {{detail}}',
    route_failed_detail: 'Routing failed: {{detail}}',
    reuse_studio_leader_plan: 'Using studio "{{name}}"; Leader is breaking down the task…',
    agent_zero_evaluating: 'Agent Zero is assessing the task domain…',
    agent_zero_direct: 'Agent Zero is handling this directly…',
    answer_done: 'Answer complete',
    creating_new_studio: 'Agent Zero is spinning up a new studio…',
    handed_to_studio_leader: 'Handed to "{{name}}"; Leader is breaking down the task…',
    leader_need_clarification:
      'The Leader needs more detail — open the task page to complete the questionnaire.',
    await_plan_review: 'Waiting for you to review the Leader\'s plan…',
    clarify_received_replanning: 'Clarifications received; Leader is replanning…',
    rerun_saved_plan: 'Re-running with the saved plan…',
    leader_dispatching: 'Leader is assigning steps and driving execution…',
    executing_step: 'Running step "{{label}}"…',
    agent0_replan_iteration: 'Agent Zero is replanning from your iteration request…',
    agent0_iterate_from_result: 'Agent Zero is planning the next iteration from the result chat…',
    scheduled_failed_detail: 'Scheduled task failed: {{detail}}',
    task_summary_done: 'Task summarization complete',
    task_summary_done_with_blockers:
      'Task summarization complete; some steps remain blocked',
    recovery_terminated:
      'The service restarted while this task was running; execution context was lost and the task was terminated.',
    retry_running: 'Re-running "{{label}}"…',
    retry_success: '"{{label}}" retry succeeded!',
    retry_still_blocked: '"{{label}}" still blocked: {{detail}}',
    edit_manual_ok: '"{{label}}" edited successfully.',
    cascade_deps_incomplete_skipped: '"{{label}}" — prerequisites incomplete; skipped.',
    cascade_deps_incomplete_deferred: '"{{label}}" — prerequisites incomplete; retry deferred.',
    cascade_running_resume: 'Resuming "{{label}}"…',
    cascade_running_rerun: 'Re-running "{{label}}"…',
    cascade_step_ok: '"{{label}}" completed successfully!',
    cascade_step_blocked: '"{{label}}" blocked: {{detail}}',
  },
}

export default en
