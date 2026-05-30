import type { ConversationDocumentsResponse, ConversationRead, DocumentRead, HoldingCreate, HoldingRead, MessageRead, PortfolioRead, ProfileRead, ProfileUpdate } from "@/lib/types"

const BASE = process.env.NEXT_PUBLIC_API_URL

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = "ApiError"
  }
}

async function apiFetch<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const body = await res.text().catch(() => "")
    throw new ApiError(res.status, body)
  }
  // DELETE returns 204 No Content — no body to parse
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function createConversation(token: string): Promise<ConversationRead> {
  return apiFetch("/conversations/", token, { method: "POST" })
}

export function listConversations(token: string): Promise<ConversationRead[]> {
  return apiFetch("/conversations/", token)
}

export function renameConversation(
  token: string,
  id: string,
  title: string,
): Promise<ConversationRead> {
  return apiFetch(`/conversations/${id}/`, token, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  })
}

export function deleteConversation(token: string, id: string): Promise<void> {
  return apiFetch<void>(`/conversations/${id}`, token, { method: "DELETE" })
}

export function listMessages(token: string, id: string): Promise<MessageRead[]> {
  return apiFetch(`/conversations/${id}/messages`, token)
}

export function getProfile(token: string): Promise<ProfileRead> {
  return apiFetch("/profile", token)
}

export function updateProfile(token: string, body: ProfileUpdate): Promise<ProfileRead> {
  return apiFetch("/profile", token, {
    method: "PUT",
    body: JSON.stringify(body),
  })
}

export async function getConversationDocuments(token: string, conversationId: string): Promise<DocumentRead[]> {
  const response = await apiFetch<ConversationDocumentsResponse>(
    `/conversations/${conversationId}/documents`,
    token,
  )
  return response.documents
}

export function listPortfolios(token: string): Promise<PortfolioRead[]> {
  return apiFetch("/portfolios/", token)
}

export function createPortfolio(token: string, name: string): Promise<PortfolioRead> {
  return apiFetch("/portfolios/", token, {
    method: "POST",
    body: JSON.stringify({ name }),
  })
}

export function deletePortfolio(token: string, portfolioId: string): Promise<void> {
  return apiFetch<void>(`/portfolios/${portfolioId}`, token, { method: "DELETE" })
}

export function addHolding(
  token: string,
  portfolioId: string,
  body: HoldingCreate,
): Promise<HoldingRead> {
  return apiFetch(`/portfolios/${portfolioId}/holdings`, token, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function deleteHolding(
  token: string,
  portfolioId: string,
  holdingId: string,
): Promise<void> {
  return apiFetch<void>(
    `/portfolios/${portfolioId}/holdings/${holdingId}`,
    token,
    { method: "DELETE" },
  )
}
