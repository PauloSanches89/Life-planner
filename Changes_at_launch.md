# Changes at launch

Running log of work required to host this app on the Azure architecture from the planner plan: Auth0 for accounts, one encrypted document per user in Cosmos DB (`id` and `userId` = Auth0 `sub`, partition key `/userId`), and Azure OpenAI `gpt-4o-mini` for the assistant plus a second deployment as the checker.

Add a new dated section when the local app changes in a way that affects production. Do not put secret values in this file.

## 2026-10-30 — baseline before hosting

The app runs as a desktop window plus an HTTP server bound to `127.0.0.1`. Documents, the master key, the remembered session, and the browser profile all live under gitignored `userdata/`. Cosmos and Azure OpenAI are optional env values and stay empty for local use.

### Host the API

- Replace the pywebview window in `main.py` as the production entry. The hosted process serves `ui/` and the JSON API on the public HTTPS origin. `webview.start`, `private_mode`, and `userdata/webview` are desktop-only.
- Stop binding `ThreadingHTTPServer` to `127.0.0.1` in `planner/server.py`. Listen on the port Azure gives the process, behind the platform's HTTPS endpoint.
- Replace the stdlib threaded server before scale-out. It is one process with in-memory login state (`_pending` PKCE verifiers, `sessions`, and one `PlannerService` per user for undo). A second instance will drop in-flight logins and split undo history.
- `dist/Life-planner/Life-planner.exe` is the older local-only build. Do not ship it as the hosted app.

### Auth0

- In the Auth0 application, set Application Origin, Allowed Callback URLs, Allowed Logout URLs, and Allowed Web Origins to the production `https` origin. Local values are `http://127.0.0.1:8765` and `http://127.0.0.1:8765/callback`.
- Set `AUTH0_CALLBACK_URL` to that callback. The code defaults it to `http://127.0.0.1:{PORT}/callback` when the variable is empty.
- Keep `AUTH0_CLIENT_SECRET` on the server only. `AUTH0_SECRET` and `APP_BASE_URL` are Auth0 SDK names; this app does not read them.
- The plan calls for checking the access token audience before a document read or write. The current login exchanges the code, calls `/userinfo`, and then discards the access token. `AUTH0_AUDIENCE` is only added to the authorize URL when it is set. Decide at launch whether same-origin server sessions stay the gate, or the API becomes a resource server that validates a JWT audience.
- Send logout through Auth0's `/v2/logout` as well as clearing `planner_session`. Today logout only drops the local session, so Auth0's own cookie can still skip the password on the next Sign in.

### Remembered sign-in

- The cookie `planner_session` is what the browser stores on the device. That part stays. It is `HttpOnly`, `SameSite=Lax`, `Path=/`, with `Max-Age` of 30 days, and it has no `Secure` flag.
- Add `Secure` once the site is HTTPS.
- The session record is not a production store. It is the process dict plus an encrypted `userdata/session.json` opened with the local master key. Move that record to a store shared by every instance (Cosmos or Azure Cache). A file on one machine will not be there after a restart on another instance.
- Give sessions an idle timeout. Expiry today is only the 30-day `expires` field.

### Documents and encryption

- Create the Cosmos database and container with partition key `/userId`. Install the `cloud` extra (`azure-cosmos`) in the hosted environment.
- Set `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, and `COSMOS_CONTAINER`. Prefer a managed identity over `COSMOS_KEY`. `open_user_store` already switches to `CosmosUserStore` when all four values are set. The item shape is unchanged: `id`, `userId`, `wrappedDek`, `dekNonce`, `nonce`, `ciphertext`.
- Set `DATA_ENCRYPTION_KEY` from Key Vault (32 bytes, base64). An empty value creates `userdata/master.key` on that machine. A new host with a new file cannot open existing items, and two instances would not share it.
- Copy local `userdata/users/*.json` items into the container once, using the same master key that wrapped them. Cosmos encryption at rest is extra; the app still decrypts only for the signed-in `sub`.
- Leave `userdata/` gitignored. Do not deploy `master.key`, `session.json`, or `webview/`.

### Azure OpenAI

- Set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_ASSISTANT_DEPLOYMENT`, and `AZURE_OPENAI_CHECKER_DEPLOYMENT`.
- The assistant deployment is `gpt-4o-mini`. The checker deployment must return `{"allow": true}` or the edit is not saved. Both keys should come from Key Vault, or the app should call Azure OpenAI with a managed identity.
- Allow the hosted app's outbound traffic to that endpoint. The model receives decrypted task text for the signed-in user only. It must not receive the master key or the per-user data key.

### Maps

- The Map tab loads `https://maps.google.com/maps?q=...&output=embed` with no API key. That avoids the Maps Embed API billing account, and it is not the supported Embed API from the plan.
- For a production embed, enable the Maps Embed API, put a referrer-restricted key on the iframe, and limit the referrer to the production origin. The key is visible in the page, so the restriction is what keeps it from being reused elsewhere.

### Configuration

- Production settings belong in the host's environment or Key Vault references, not a deployed `.env`.
- Names are listed in `.env.example`. Fill Auth0, `DATA_ENCRYPTION_KEY`, both Azure OpenAI deployments, and the Cosmos values. Leave local-only files out of the package.

## 2026-10-30 — chat can use Gemini locally

When `GEMINI_API_KEY` is set, the assistant and the checker both call `gemini-2.5-flash` (or `GEMINI_MODEL`) instead of Azure OpenAI. The key stays on the server. At launch, pick one host:

- Keep Gemini. Store `GEMINI_API_KEY` in Key Vault. The free tier is a property of the Google Cloud project, not the key. A project marked "free tier unavailable" rejects `generateContent` until billing is enabled on that project. A second key in the same project does not change that.
- Or leave `GEMINI_API_KEY` empty and use the Azure OpenAI deployments in the section above.

The model still receives decrypted task text for the signed-in user only, and still must not receive the master key or the per-user data key.

## 2026-10-30 — local chat can use Ollama

When `OLLAMA_MODEL` is set, the assistant and the checker call that model on `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`) and skip Gemini and Azure OpenAI. The model weights stay on the machine that runs Ollama.

At launch, a hosted Azure app does not include Ollama. Pick one:

- Run Ollama beside the app on a machine you control, and set `OLLAMA_MODEL` there. Chat stops working on any instance that cannot reach that address.
- Or leave `OLLAMA_MODEL` empty and use Gemini or the Azure OpenAI deployments above.

The decrypted task text for the signed-in user is still what the model receives. The master key and the per-user data key stay out of the prompt.
