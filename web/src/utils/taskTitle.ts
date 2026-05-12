import type { Task } from '../types'

export function extractTaskQuestionTitle(question: string): string {
  const clean = question
    .replace(/^\[(?:目标工作室：|目标团队：|Target studio:|Target team:)[^\]]+\]\s*/, '')
    .trim()
  return clean.length > 60 ? `${clean.slice(0, 60)}…` : clean
}

export function getTaskDisplayTitle(task: Pick<Task, 'question' | 'subject'>): string {
  return task.subject?.trim() || extractTaskQuestionTitle(task.question)
}
