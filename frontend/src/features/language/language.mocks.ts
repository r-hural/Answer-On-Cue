import { http, HttpResponse, type HttpHandler } from 'msw'
import type { Subject } from '../library/api'

/** GET /api/subjects/:id for one subject. */
export function subjectHandler(subject: Partial<Subject> & { id: number }): HttpHandler {
  return http.get(`/api/subjects/${subject.id}`, () =>
    HttpResponse.json({ name: 'Subject', description: null, document_count: 0, ...subject }),
  )
}
