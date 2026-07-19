import type { paths } from "./schema";

type PathTemplate = keyof paths & string;
type MethodPath<Method extends string> = {
  [Path in PathTemplate]: paths[Path] extends Record<Method, infer Operation>
    ? [Operation] extends [never]
      ? never
      : Path
    : never;
}[PathTemplate];

type GetPath = MethodPath<"get">;
type PostPath = MethodPath<"post">;
type PatchPath = MethodPath<"patch">;
type OperationFor<
  Method extends string,
  Path extends string,
> = Path extends PathTemplate
  ? paths[Path] extends Record<Method, infer Operation>
    ? Operation
    : never
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
type ParametersFor<Operation> = Operation extends { parameters: infer Params }
  ? Params
  : never;
type ParameterKind = "path" | "query";
type PresentParameterKind<Params> = {
  [Kind in ParameterKind]: Kind extends keyof Params
    ? [Exclude<Params[Kind], undefined>] extends [never]
      ? never
      : Kind
    : never;
}[ParameterKind];
type RequiredParameterKind<Params> = {
  [Kind in PresentParameterKind<Params>]: Params extends Required<
    Pick<Params, Kind>
  >
    ? Kind
    : never;
}[PresentParameterKind<Params>];
type ParameterGroups<Params> = {
  [Kind in RequiredParameterKind<Params>]: Exclude<Params[Kind], undefined>;
} & {
  [
    Kind in Exclude<PresentParameterKind<Params>, RequiredParameterKind<Params>>
  ]?: Exclude<Params[Kind], undefined>;
};
type RequestOptions<Operation> = {
  signal?: AbortSignal;
} & ([PresentParameterKind<ParametersFor<Operation>>] extends [never]
  ? { params?: never }
  : [RequiredParameterKind<ParametersFor<Operation>>] extends [never]
    ? { params?: ParameterGroups<ParametersFor<Operation>> }
    : { params: ParameterGroups<ParametersFor<Operation>> });
type OptionsArguments<Operation> = [
  RequiredParameterKind<ParametersFor<Operation>>,
] extends [never]
  ? [options?: RequestOptions<Operation>]
  : [options: RequestOptions<Operation>];
type ErrorFields = Record<string, unknown>;

interface RuntimeOptions {
  params?: {
    path?: Record<string, unknown>;
    query?: Record<string, unknown>;
  };
  signal?: AbortSignal;
}

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
  path: string,
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

function requestPath(template: string, options?: RuntimeOptions): string {
  const path = template.replace(
    /\{([^}]+)\}/g,
    (_placeholder, name: string) => {
      const value = options?.params?.path?.[name];
      if (value === undefined || value === null || value === "") {
        throw new TypeError(`Missing path parameter: ${name}`);
      }
      return encodeURIComponent(String(value));
    },
  );
  const query = new URLSearchParams();
  for (const [name, rawValue] of Object.entries(options?.params?.query ?? {})) {
    if (rawValue === undefined || rawValue === null) {
      continue;
    }
    const values = Array.isArray(rawValue) ? rawValue : [rawValue];
    for (const value of values) {
      query.append(name, String(value));
    }
  }
  const encodedQuery = query.toString();
  return encodedQuery === "" ? path : `${path}?${encodedQuery}`;
}

function write<Result>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
  idempotencyKey: string,
  options?: RuntimeOptions,
): Promise<Result> {
  return request<Result>(requestPath(path, options), {
    method,
    body: JSON.stringify(body),
    signal: options?.signal,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
  });
}

export function newIdempotencyKey(): string {
  return `portfolio-${crypto.randomUUID()}`;
}

async function get<Path extends GetPath>(
  path: Path,
  ...[options]: OptionsArguments<OperationFor<"get", Path>>
): Promise<SuccessfulJson<OperationFor<"get", Path>>> {
  return request<SuccessfulJson<OperationFor<"get", Path>>>(
    requestPath(path, options as RuntimeOptions | undefined),
    {
      method: "GET",
      signal: options?.signal,
    },
  );
}

async function post<Path extends PostPath>(
  path: Path,
  ...args: [
    ...WriteArguments<OperationFor<"post", Path>>,
    ...OptionsArguments<OperationFor<"post", Path>>,
  ]
): Promise<SuccessfulJson<OperationFor<"post", Path>>> {
  const body = args[0] as unknown;
  const idempotencyKey = args[1] as string;
  const options = args[2] as RuntimeOptions | undefined;
  return write<SuccessfulJson<OperationFor<"post", Path>>>(
    "POST",
    path,
    body,
    idempotencyKey,
    options,
  );
}

async function patch<Path extends PatchPath>(
  path: Path,
  ...args: [
    ...WriteArguments<OperationFor<"patch", Path>>,
    ...OptionsArguments<OperationFor<"patch", Path>>,
  ]
): Promise<SuccessfulJson<OperationFor<"patch", Path>>> {
  const body = args[0] as unknown;
  const idempotencyKey = args[1] as string;
  const options = args[2] as RuntimeOptions | undefined;
  return write<SuccessfulJson<OperationFor<"patch", Path>>>(
    "PATCH",
    path,
    body,
    idempotencyKey,
    options,
  );
}

export const api = { get, post, patch };
