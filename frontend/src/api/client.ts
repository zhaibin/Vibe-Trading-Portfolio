import type { paths } from "./schema";

type PathTemplate = keyof paths & string;
type ExpandPath<Path extends string> =
  Path extends `${infer Start}{${string}}${infer Rest}`
    ? `${Start}${string}${ExpandPath<Rest>}`
    : Path;
type MethodPath<Method extends string> = {
  [Path in PathTemplate]: paths[Path] extends Record<Method, infer Operation>
    ? [Operation] extends [never]
      ? never
      : ExpandPath<Path>
    : never;
}[PathTemplate];
type WithQuery<Path extends string> = Path | `${Path}?${string}`;

type GetPath = WithQuery<MethodPath<"get">>;
type PostPath = MethodPath<"post">;
type PatchPath = MethodPath<"patch">;
type ApiRequestPath = WithQuery<ExpandPath<PathTemplate>>;
type TemplateFor<Path extends string> = {
  [Template in PathTemplate]: Path extends `${infer Base}?${string}`
    ? Base extends ExpandPath<Template>
      ? Template
      : never
    : Path extends ExpandPath<Template>
      ? Template
      : never;
}[PathTemplate];
type OperationFor<Method extends string, Path extends string> =
  paths[TemplateFor<Path>] extends Record<Method, infer Operation>
    ? Operation
    : never;
type JsonRequestBody<Operation> = Operation extends {
  requestBody: { content: { "application/json": infer Body } };
}
  ? Body
  : never;
type SuccessfulStatus =
  200 | 201 | 202 | 203 | 204 | 205 | 206 | 207 | 208 | 226;
type JsonResponse<Response> = Response extends {
  content: { "application/json": infer Result };
}
  ? Result
  : undefined;
type SuccessfulJson<Operation> = Operation extends {
  responses: infer Responses;
}
  ? {
      [Status in keyof Responses]: Status extends SuccessfulStatus
        ? JsonResponse<Responses[Status]>
        : never;
    }[keyof Responses]
  : never;
type WriteArguments<Operation> = [JsonRequestBody<Operation>] extends [never]
  ? [body: undefined, idempotencyKey: string]
  : [body: JsonRequestBody<Operation>, idempotencyKey: string];
type ErrorFields = Record<string, unknown>;

const invalidJson = Symbol("invalid-json");

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readJson(
  response: Response,
): Promise<unknown | typeof invalidJson> {
  const body = await response.text();
  if (body === "") {
    return undefined;
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return invalidJson;
  }
}

function errorDetail(
  payload: unknown,
): { code: string; fields?: ErrorFields } | undefined {
  if (
    !isRecord(payload) ||
    !isRecord(payload.error) ||
    typeof payload.error.code !== "string"
  ) {
    return undefined;
  }
  return {
    code: payload.error.code,
    fields: isRecord(payload.error.fields) ? payload.error.fields : undefined,
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields?: ErrorFields;

  constructor(status: number, code: string, fields?: ErrorFields) {
    super(`API request failed with ${status} (${code})`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

async function request<Result>(
  path: ApiRequestPath,
  init: RequestInit,
): Promise<Result> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  const payload = await readJson(response);

  if (!response.ok) {
    const detail = payload === invalidJson ? undefined : errorDetail(payload);
    throw new ApiError(
      response.status,
      detail?.code ?? "HTTP_ERROR",
      detail?.fields,
    );
  }
  if (payload === invalidJson) {
    throw new ApiError(response.status, "INVALID_RESPONSE");
  }
  return payload as Result;
}

function write<Result>(
  method: "POST" | "PATCH",
  path: PostPath | PatchPath,
  body: unknown,
  idempotencyKey: string,
): Promise<Result> {
  return request<Result>(path, {
    method,
    body: JSON.stringify(body),
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
  });
}

export function newIdempotencyKey(): string {
  return `portfolio-${crypto.randomUUID()}`;
}

function get<Path extends GetPath>(
  path: Path,
): Promise<SuccessfulJson<OperationFor<"get", Path>>> {
  return request<SuccessfulJson<OperationFor<"get", Path>>>(path, {
    method: "GET",
  });
}

function post<Path extends PostPath>(
  path: Path,
  ...args: WriteArguments<OperationFor<"post", Path>>
): Promise<SuccessfulJson<OperationFor<"post", Path>>> {
  const [body, idempotencyKey] = args;
  return write<SuccessfulJson<OperationFor<"post", Path>>>(
    "POST",
    path,
    body,
    idempotencyKey,
  );
}

function patch<Path extends PatchPath>(
  path: Path,
  ...args: WriteArguments<OperationFor<"patch", Path>>
): Promise<SuccessfulJson<OperationFor<"patch", Path>>> {
  const [body, idempotencyKey] = args;
  return write<SuccessfulJson<OperationFor<"patch", Path>>>(
    "PATCH",
    path,
    body,
    idempotencyKey,
  );
}

export const api = { get, post, patch };
