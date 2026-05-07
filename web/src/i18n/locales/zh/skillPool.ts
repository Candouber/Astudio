import type { TranslationTree } from '../../types'

const zh: TranslationTree = {
  skillPool: {
    opFailed: '操作失败',
    builtin: '内置',
    bundleKind: 'Bundle',
    aiGenerated: 'AI 生成',
    categoryImport: '导入',
    categoryCustom: '自定义',
    categoryGeneral: '通用',
    generatedOk: '已生成',
    title: 'Skill 池',
    introP1:
      '统一维护员工可分配的公共技能。Agent 通过 use_skill(slug) 加载 Skill 包后按 SKILL.md 指南执行。',
    introP2:
      'Skill 的两种来源：①「从 URL 导入」贴一个 ClawHub / SkillHub skill 页地址直接落地；②「AI 生成」让 skill_creator 按你的目标自动造一份本地 SKILL.md。',
    refresh: '刷新',
    importTitle: '从 URL 导入 Skill',
    exploreHint: '去这两个地方探索skill吧',
    urlPlaceholder: '粘贴 ClawHub / SkillHub 的 skill 页 URL',
    probing: '识别来源中…',
    willBeSlug: '将落地为 slug:',
    slugPlaceholder: '本地 slug（留空则自动生成 user__slug）',
    categoryPlaceholder: '分类（默认：导入）',
    importBtn: '导入',
    aiTitle: '让模型生成一个 Skill',
    toggleHide: '收起',
    toggleShow: '展开',
    aiDesc:
      '把你想要的能力描述清楚（输入、处理步骤、输出），skill_creator 会生成 SKILL.md 并注册。',
    aiSlugPh: 'slug（英文 / 数字 / 下划线）',
    aiNamePh: '展示名称',
    aiCategoryPh: '分类（默认：自定义）',
    aiGoalPh:
      '示例：从用户上传的 CSV 中统计字段覆盖率和异常值，输出 Markdown 报告，含前 10 个异常行。',
    generateBtn: '生成 Skill',
    loadingPool: '加载 Skill 池...',
    expandCategory: '展开分类',
    collapseCategory: '收起分类',
    toggleOn: '启用',
    toggleOff: '停用',
    noDesc: '（无描述）',
    pythonImpl: '平台 Python 实现，可直接 function-call',
    filesSummary: '{{n}} 个文件',
    filesMore: '... 还有 {{n}} 个',
    viewSource: '查看来源 ↗',
    viewSkillMd: '查看 SKILL.md',
    refreshFromSource: '从来源重新拉取最新版本',
    deleteTitle: '删除',
    assignable: '可分配',
    footerHint:
      '想找合适的 Skill？在任意任务中让 agent 调用 find_skill，它会返回 ClawHub 的候选列表供你导入。',
    drawerAria: 'SKILL.md 预览',
    close: '关闭',
    loadingMd: '加载 SKILL.md…',
    deleteConfirm: '确认删除 {{slug}}？已分配给员工的记录不会自动解绑。',
  },
}

export default zh
