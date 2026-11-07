import { randomUUID } from "node:crypto";
import { Readable, Writable } from "node:stream";
import { pathToFileURL } from "node:url";
import {
	agent,
	ndJsonStream,
	PROTOCOL_VERSION,
	RequestError,
} from "@agentclientprotocol/sdk";

const MAX_TEXT_BYTES = 10_000_000;

// Pi's model runtime supplies native OAuth refresh and catalogs, without loading tools or extensions.
export function createPiAgent(runtime, nativeModel) {
	const separator = nativeModel.indexOf("/");
	const providerId = nativeModel.slice(0, separator);
	const modelId = nativeModel.slice(separator + 1);
	if (separator < 1 || !modelId || modelId === "default") {
		throw new Error("Pi requires an explicit provider/model.");
	}
	const provider = runtime.getProvider(providerId);
	const model = runtime.getModel(providerId, modelId);
	const apiRoute = providerId === "openrouter";
	let sessionId;
	let controller;
	const configOptions = [
		{
			id: "model",
			name: "Model",
			category: "model",
			type: "select",
			currentValue: nativeModel,
			options: model ? [{ value: nativeModel, name: model.name }] : [],
		},
	];
	const safe = (handler) => async (context) => {
		try {
			return await handler(context);
		} catch (error) {
			if (error instanceof RequestError) throw error;
			if (error?.code === "oauth" || error?.code === "auth")
				throw RequestError.authRequired();
			throw RequestError.internalError(
				undefined,
				"Pi request failed. Check its login and quota.",
			);
		}
	};
	const checkAuth = async (client, signal) => {
		const auth = await runtime.checkAuth(providerId, { signal });
		const accepted = apiRoute
			? auth?.type === "api_key" && auth.source === "stored credential"
			: provider?.auth.oauth?.isSubscription === true && auth?.type === "oauth";
		await client.notify("_auth/status_update", {
			authStatus: {
				kind: accepted ? (apiRoute ? "api_key" : "account") : "none",
			},
		});
		if (!accepted) throw RequestError.authRequired();
	};
	const checkSession = (requestedId) => {
		if (!sessionId || requestedId !== sessionId)
			throw RequestError.sessionNotFound(requestedId);
	};
	return agent()
		.onRequest(
			"initialize",
			safe(async ({ client, signal }) => {
				await checkAuth(client, signal);
				return {
					protocolVersion: PROTOCOL_VERSION,
					agentInfo: { name: "llm-council-pi", version: "0.1.0" },
					agentCapabilities: { _meta: { authStatus: {} } },
				};
			}),
		)
		.onRequest(
			"session/new",
			safe(async ({ params, client, signal }) => {
				if (sessionId || params.mcpServers.length || !model)
					throw RequestError.invalidParams();
				await checkAuth(client, signal);
				sessionId = randomUUID();
				return { sessionId, configOptions };
			}),
		)
		.onRequest(
			"session/set_config_option",
			safe(({ params }) => {
				checkSession(params.sessionId);
				if (params.configId !== "model" || params.value !== nativeModel)
					throw RequestError.invalidParams();
				return { configOptions };
			}),
		)
		.onRequest(
			"session/prompt",
			safe(async ({ params, client, signal }) => {
				checkSession(params.sessionId);
				if (controller || params.prompt.some((block) => block.type !== "text"))
					throw RequestError.invalidParams();
				const text = params.prompt.map((block) => block.text).join("\n");
				if (!text.trim() || Buffer.byteLength(text) > MAX_TEXT_BYTES)
					throw RequestError.invalidParams();
				controller = new AbortController();
				try {
					const requestSignal = AbortSignal.any([signal, controller.signal]);
					await checkAuth(client, requestSignal);
					const resolved = await runtime.getAuth(model, {
						signal: requestSignal,
					});
					// Use the verified resolution so concurrent credential changes cannot switch the request's billing route.
					if (resolved?.source !== (apiRoute ? "stored credential" : "OAuth"))
						throw RequestError.authRequired();
					const stream = provider.streamSimple(
						{ ...model, baseUrl: resolved.auth.baseUrl ?? model.baseUrl },
						{
							messages: [
								{ role: "user", content: text, timestamp: Date.now() },
							],
							tools: [],
						},
						{ ...resolved.auth, env: resolved.env, signal: requestSignal },
					);
					let size = 0;
					for await (const event of stream) {
						if (event.type === "text_delta" || event.type === "thinking_delta")
							size += Buffer.byteLength(event.delta);
						if (size > MAX_TEXT_BYTES) {
							controller.abort();
							throw RequestError.internalError(
								undefined,
								"Pi response exceeded the output limit.",
							);
						}
					}
					const result = await stream.result();
					if (requestSignal.aborted) return { stopReason: "cancelled" };
					if (
						result.stopReason !== "stop" ||
						result.provider !== providerId ||
						result.model !== modelId ||
						result.content.some((block) => block.type === "toolCall")
					) {
						throw RequestError.internalError(
							undefined,
							"Pi did not complete an answer with the selected model.",
						);
					}
					const answer = result.content
						.filter((block) => block.type === "text")
						.map((block) => block.text)
						.join("");
					if (!answer.trim() || Buffer.byteLength(answer) > MAX_TEXT_BYTES)
						throw RequestError.internalError();
					await client.notify("session/update", {
						sessionId,
						update: {
							sessionUpdate: "agent_message_chunk",
							content: { type: "text", text: answer },
						},
					});
					return { stopReason: "end_turn" };
				} finally {
					controller = undefined;
				}
			}),
		)
		.onNotification("session/cancel", ({ params }) => {
			if (params.sessionId === sessionId) controller?.abort();
		});
}

if (
	process.argv[1] &&
	import.meta.url === pathToFileURL(process.argv[1]).href
) {
	try {
		const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
		const runtime = await ModelRuntime.create({
			modelsPath: null,
			refreshOnCreate: false,
		});
		const connection = createPiAgent(runtime, process.argv[2] ?? "").connect(
			ndJsonStream(
				Writable.toWeb(process.stdout),
				Readable.toWeb(process.stdin),
				{ maxMessageBytes: MAX_TEXT_BYTES * 6 },
			),
		);
		await connection.closed;
	} catch {
		process.stderr.write(
			"Pi adapter failed. Check the pinned installation, model, and native login.\n",
		);
		process.exitCode = 1;
	}
}
