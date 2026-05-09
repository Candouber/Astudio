import type { Annotation } from '../types'

export const OUTPUT_ANNOTATION_PREFIX = '__output__:'

export function getAnnotationTargetType(annotation: Annotation): 'node' | 'output' | 'annotation' {
  const explicit = annotation.target_type?.trim()
  if (explicit === 'node' || explicit === 'output' || explicit === 'annotation') return explicit
  if (annotation.parent_annotation_id) return 'annotation'
  if (annotation.node_id.startsWith(OUTPUT_ANNOTATION_PREFIX)) return 'output'
  return 'node'
}

export function getAnnotationTargetId(annotation: Annotation): string {
  const explicit = annotation.target_id?.trim()
  if (explicit) return explicit
  const type = getAnnotationTargetType(annotation)
  if (type === 'annotation') return annotation.parent_annotation_id || annotation.node_id
  if (type === 'output' && annotation.node_id.startsWith(OUTPUT_ANNOTATION_PREFIX)) {
    return annotation.node_id.slice(OUTPUT_ANNOTATION_PREFIX.length)
  }
  return annotation.node_id
}

export function annotationTargetsNode(annotation: Annotation, nodeId: string): boolean {
  return getAnnotationTargetType(annotation) === 'node' && getAnnotationTargetId(annotation) === nodeId
}

export function annotationTargetsOutput(annotation: Annotation, path: string): boolean {
  return getAnnotationTargetType(annotation) === 'output' && getAnnotationTargetId(annotation) === path
}

export function annotationTargetsAnnotation(annotation: Annotation, annotationId: string): boolean {
  return getAnnotationTargetType(annotation) === 'annotation' && getAnnotationTargetId(annotation) === annotationId
}
