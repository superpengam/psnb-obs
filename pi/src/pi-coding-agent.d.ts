/**
 * 本地类型声明 —— 只在没装 @earendil-works/pi-coding-agent 时兜底。
 *
 * 接口对齐 pi-redkit / pi-cline 的实际用法（before_agent_start /
 * before_provider_headers / before_provider_request / after_provider_response /
 * registerCommand）。装了真包时，真包的类型优先。
 */

declare module "@earendil-works/pi-coding-agent" {
	export interface AgentStartEvent {
		systemPrompt: string;
	}

	export interface HeadersEvent {
		headers: Record<string, string>;
	}

	export interface ProviderRequestEvent {
		payload: unknown;
	}

	export interface ProviderResponseEvent {
		status: number;
		headers?: Record<string, string>;
	}

	export interface HookContext {
		model?: string;
		provider?: string;
		hasUI?: boolean;
		ui: { notify(message: string, level?: "info" | "warning" | "error"): void };
	}

	export interface Completion {
		value: string;
		label: string;
	}

	export interface CommandSpec {
		description: string;
		getArgumentCompletions?: (prefix: string) => Completion[] | null;
		handler: (args: string, ctx: ExtensionCommandContext) => void | Promise<void>;
	}

	export interface ExtensionCommandContext extends HookContext {}

	export interface ExtensionAPI {
		on(event: "before_agent_start", handler: (event: AgentStartEvent, ctx: HookContext) => unknown): void;
		on(event: "before_provider_headers", handler: (event: HeadersEvent, ctx: HookContext) => unknown): void;
		on(event: "before_provider_request", handler: (event: ProviderRequestEvent, ctx: HookContext) => unknown): void;
		on(event: "after_provider_response", handler: (event: ProviderResponseEvent, ctx: HookContext) => unknown): void;
		registerCommand(name: string, spec: CommandSpec): void;
	}
}
