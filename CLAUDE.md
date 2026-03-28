# Vaultic — Coding Standards

These standards apply to all contributors — human or AI. They exist to prevent the classes of bugs and code quality issues found during the March 2026 peer review.

---

## Project Structure

```
api/                  # FastAPI backend
  routers/            # One file per domain (accounts, budget, tax, etc.)
  sage_tools.py       # Sage tool dispatch functions (keep out of sage.py)
  main.py             # App factory, middleware, lifespan
tests/                # pytest unit tests (one test_*.py per router)
ui/
  src/
    pages/            # Top-level route components (thin orchestrators)
    components/       # Shared + domain-grouped sub-components
      budget/         # Budget-specific components
      dashboard/      # Dashboard-specific components
      taxes/          # Tax-specific components
    api.js            # All backend API call functions
    App.jsx           # Router + layout shell
    App.css           # Global styles + utility classes
  tests/e2e/          # Playwright E2E tests
    helpers.js        # Shared mock API setup
deploy/               # Deployment configs (systemd, nginx, litestream)
```

---

## Component Size & Extraction Rules

**Hard limit: 400 lines per JSX file.** If a component exceeds this, extract sub-components.

### Extraction patterns

| Pattern | When to use | Props shape |
|---------|------------|-------------|
| **Pure display** | Renders data, no state or API calls | `{ data }` — parent owns all state |
| **Self-contained section** | Has its own API calls, loading state, handlers | `{ id, onUpdate }` — fetches its own data |
| **Modal** | Dialog with internal form state | `{ open, onClose }` — parent only controls visibility |

### What stays in the parent page

- Page-level layout and section ordering
- Data that multiple children need (fetch once, pass down)
- Cross-component coordination (e.g., drag state shared between siblings)
- Modal open/close state (the modal itself manages its internal form state)

### Naming

- Pages: `ui/src/pages/PageName.jsx`
- Domain components: `ui/src/components/domain/ComponentName.jsx`
- Shared components: `ui/src/components/ComponentName.jsx`
- Utility/helper files: `ui/src/components/domain/domainUtils.jsx`

---

## CSS & Styling

**Prefer CSS utility classes over inline styles.** Utility classes are defined in `App.css`.

### Available utility classes

| Class | Purpose |
|-------|---------|
| `.flex-between`, `.flex-center` | Flex layout patterns |
| `.gap-8`, `.gap-10`, `.gap-12` | Flex/grid gap |
| `.flex-wrap` | Flex wrapping |
| `.table-header-row`, `.th-cell`, `.td-cell`, `.tr-row` | Table styling |
| `.th-cell.right`, `.td-cell.bold`, `.td-cell.dim`, `.td-cell.negative`, `.td-cell.positive` | Table cell modifiers |
| `.metric-tile`, `.metric-tile .label`, `.metric-tile .value` | Dashboard metric cards |
| `.status-banner`, `.status-banner.ok`, `.status-banner.warn` | Status banners |
| `.btn-upload`, `.btn-purple` | Button variants |
| `.section-label`, `.sub-label` | Text label patterns |
| `.card` | Standard card container |
| `.form-input` | Form input styling |

### When inline styles are acceptable

- One-off layout values (`marginBottom: 20`, `width: 130`)
- Dynamic/computed values (`opacity: q.status === "past" ? 0.6 : 1`)
- Conditional colors (`color: isPositive ? "var(--green)" : "var(--red)"`)

### When to create a new utility class

- When you see the same inline style pattern in 3+ places
- Add it to `App.css` under the appropriate section with a comment

### Always use CSS variables for colors

```jsx
// Good
color: "var(--text2)"
background: "var(--bg3)"

// Bad
color: "#8b92a8"
background: "#1e2330"
```

---

## Backend (Python / FastAPI)

### Router organization

- One router file per domain in `api/routers/`
- **Route order matters**: Define specific sub-routes BEFORE wildcard/parameterized routes
  ```python
  # Good — specific first
  @router.patch("/groups/reorder")
  @router.patch("/groups/{group_id}")

  # Bad — wildcard catches "reorder" as group_id
  @router.patch("/groups/{group_id}")
  @router.patch("/groups/reorder")
  ```

### Request/response models

- Use **Pydantic models** for all request bodies — never `body: dict`
  ```python
  # Good
  class AssignRequest(BaseModel):
      transaction_id: str
      item_id: int

  @router.post("/assign")
  def assign(req: AssignRequest): ...

  # Bad
  @router.post("/assign")
  def assign(body: dict): ...
  ```

- Use consistent response format: `{"ok": True, ...}` for mutations

### Sage tools

- All Sage tool functions live in `api/sage_tools.py` with a `TOOL_DISPATCH` dict
- `sage.py` calls `_call_tool()` which dispatches via the dict — keep the dispatcher under 15 lines
- Never add tool logic directly to `sage.py`

### Error handling

- Let FastAPI's built-in exception handling work — don't wrap everything in try/except
- Use `HTTPException` with appropriate status codes
- Only catch exceptions at system boundaries (external API calls, file I/O)

### Tax year and dynamic values

- Never hardcode tax years, brackets, or deduction amounts
- Use the current year or accept year as a parameter
- Tax brackets and standard deductions should be data-driven

---

## Frontend (React 18)

### React 18 StrictMode

StrictMode double-invokes effects in development. This means:

```jsx
// Good — reset ref on remount
useEffect(() => {
  mountedRef.current = true;
  loadData();
  return () => { mountedRef.current = false; };
}, []);

// Bad — ref stays false after StrictMode cleanup
useEffect(() => {
  loadData();
  return () => { mountedRef.current = false; };
}, []);
```

### Performance

- Use `React.memo()` on components that receive stable props but re-render due to parent state changes (e.g., chat message lists)
- Memoize expensive computations with `useMemo`
- Use `useCallback` for handlers passed to memoized children

### HTML5 Drag and Drop

**Never call `setState` during a `dragstart` handler.** React re-renders mutate the DOM, which cancels the browser's drag operation.

```jsx
// Good — use DOM manipulation, not state
function handleDragStart(e) {
  e.dataTransfer.setData("text/plain", id);
  document.body.classList.add("dragging");
}

// Bad — causes re-render that cancels drag
function handleDragStart(e) {
  e.dataTransfer.setData("text/plain", id);
  setDragId(id);  // DO NOT DO THIS
}
```

### Accessibility

- All `<th>` elements must have `scope="col"` or `scope="row"`
- Interactive elements need `aria-label` when text content isn't descriptive
- Use semantic HTML (`<button>`, `<nav>`, `<main>`) over styled `<div>`s

---

## Testing

### Unit tests (pytest)

- Every router should have a corresponding `tests/test_<router>.py`
- Test auth guards (401 without token, 403 for wrong user)
- Test edge cases and error paths, not just happy paths
- Gate APScheduler behind `TESTING` env var to prevent test hangs:
  ```python
  if not os.environ.get("TESTING"):
      scheduler.start()
  ```

### E2E tests (Playwright)

All E2E mocks live in `ui/tests/e2e/helpers.js`.

**Route registration order**:
1. Register the catch-all `**/api/**` route FIRST (returns `{}`)
2. Register specific sub-routes before base routes
3. Always use `**/` prefix on route patterns for full URL matching

```javascript
// Good — catch-all first, then specific, then base
await page.route("**/api/**", r => r.fulfill({ json: {} }));
await page.route("**/api/accounts/transactions/recent*", r => r.fulfill({ json: [] }));
await page.route("**/api/accounts", r => r.fulfill({ json: [...] }));

// Bad — base route intercepts sub-routes; missing catch-all causes ECONNREFUSED
await page.route("**/api/accounts", r => r.fulfill({ json: [...] }));
await page.route("**/api/accounts/transactions/recent*", r => r.fulfill({ json: [] }));
```

**Mock response formats** must match what the frontend actually expects:
```javascript
// Sage chat — frontend reads response.response, not response.reply
r.fulfill({ json: { response: "Hello!", history: [] } });

// Sage speak — returns audio blob, not JSON
r.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from([]) });
```

**Collapsible nav groups**: If a link is inside a collapsed sidebar group, expand the group first:
```javascript
await page.getByRole("button", { name: /finance/i }).click();
await page.getByRole("link", { name: /transactions/i }).click();
```

### Test coverage expectations

- New routers: auth guards + CRUD + edge cases
- New components: at least one E2E test verifying render and basic interaction
- Bug fixes: add a regression test

---

## Deployment & Infrastructure

### Deploy safety

- `deploy.yml` (GitHub Actions) deploys via SSH — changes are live immediately
- Always test locally before pushing to main
- `nginx` config changes require manual reload — CI/CD only restarts the Python process
- Set `client_max_body_size 25m` in nginx for PDF uploads (default 1MB blocks them)

### Litestream backup

- SQLite WAL mode with Litestream → Cloudflare R2 continuous replication
- `start.sh` wraps uvicorn via `litestream replicate -exec`
- Never modify `start.sh` grep patterns without testing — safe line-by-line read, not grep

### journald

- Log rotation configured in `deploy.yml` — keep 7 days, 500MB max
- Don't add custom log rotation scripts

---

## Security Practices

- **SQL**: Always use parameterized queries — never f-strings or string concatenation
- **Auth**: Every endpoint must check JWT token; use `get_current_user` dependency
- **Secrets**: Never commit `.env`, credentials, or API keys. Use `.env.example` for templates
- **File uploads**: Validate file types server-side; don't trust client `Content-Type`
- **CORS**: Restrict to known origins in production
- **Rate limiting**: Sage has 60 msg/hour per user — don't remove or increase without consideration

---

## Code Style

### Python
- Follow existing patterns in the codebase
- Use type hints for function signatures
- Helper functions defined BEFORE first use (especially in f-strings)
- `load_dotenv()` must be called before any import that reads `os.environ` at module load

### JavaScript/JSX
- Functional components only (no class components)
- Destructure props in function signature
- Use `const` by default, `let` only when reassignment is needed
- Format currency with `toLocaleString("en-US", { style: "currency", currency: "USD" })`

### General
- No dead code — if it's unused, delete it (don't comment it out)
- No `console.log` in committed code (use proper error handling)
- Keep imports organized: external libraries first, then internal modules
- One component per file
