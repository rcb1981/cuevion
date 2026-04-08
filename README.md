# Cuevion

## Architecture Note

- Vercel production Root Directory is `frontend/`
- The active production source tree is `frontend/src/`
- Root-level `src/` is currently inactive for production and should not be used for feature work unless the deployment architecture is changed

## Active App Paths

- Production app entry: [`frontend/src/main.tsx`](frontend/src/main.tsx)
- Production app shell: [`frontend/src/App.tsx`](frontend/src/App.tsx)
- Production workspace shell: [`frontend/src/components/workspace/WorkspaceShell.tsx`](frontend/src/components/workspace/WorkspaceShell.tsx)

## Duplicate Tree Warning

- `src/` contains an inactive duplicate app tree kept only for now as a non-production copy
- Before editing app behavior, prefer checking `frontend/src/` first
