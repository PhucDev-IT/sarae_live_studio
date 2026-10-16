# Sarae React UI Core — integrated

Source: https://github.com/PhucDev-IT/react-ui-core/tree/dev

The library is now a **pinned Git submodule** at `vendor/react-ui-core`.
It is not installed as an npm package and its source is not copied into Sarae Live Studio.

## Clone / update (Windows CMD)

```cmd
git checkout dev
git pull origin dev
git submodule update --init --recursive
cd web
npm.cmd install
npm.cmd run build
npm.cmd run dev
```

For new clones, prefer `git clone --recurse-submodules -b dev <repository-url>`.

The Web build resolves `@sarae/ui` through Vite aliases and TypeScript paths.
The shared SCSS is imported in `web/src/main.tsx`; Sass and source-package dependencies are declared in `web/package.json`.

## Integration in the current UI

- `ThemeProvider` synchronizes UI Core light/dark mode with Sarae's existing theme toggle.
- Primary 'Quản lý video' and module back button use UI Core `Button`.
- Settings note field uses UI Core `Input` including label/prefix.
- Typography/control tokens follow UI Core but retain Sarae's own purple palette and custom dashboard illustrations.

## Guidance for subsequent modules

Prefer `@sarae/ui` components for Field, Input, Select, Button, Modal, FileUpload, DataTable and Toast.
Compose product/media business behavior in this application's own source rather than extending the shared component library.

The submodule is pinned to a specific `react-ui-core/dev` commit. Update deliberately with `git submodule update --remote vendor/react-ui-core`, review changes, then commit the new gitlink.

## Validation

Run `npm.cmd run build` and `npm.cmd run lint` from `web` and inspect at mobile/tablet/desktop sizes after fetching the submodule. No claim of successful local runtime build is made from remote source edits alone.
