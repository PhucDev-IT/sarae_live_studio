# React UI Core adoption

Source: https://github.com/PhucDev-IT/react-ui-core/tree/dev

## Phase 1 (applied)
The current Web Admin is visually approved. Keep its existing purple branding, icons, artwork and layout. Normalize text size, control height and spacing using design tokens from `packages/ui/src/theme/_tokens.scss` (13px, 14.5px, 15.5px, 20px; 44px controls).

The stylesheet currently mirrors the typography/spacing rules; **the UI Core package is not yet installed or linked**.

## Phase 2 (form development)
Integrate the source library as documented in the library's `CONSUMING.md`, ideally as a version-pinned Git submodule with source aliases and Sass support. Avoid copying component source into this app. Resolve `@sarae/ui` and `@sarae/ui/styles`, then import styles once at the app entry.

Prefer real library components for:
- Button, Input, Select, Textarea, Field
- FileUpload, Dropzone, TagsInput
- Modal, ConfirmModal, Toast, EmptyState
- FilterBar, DataTable, Pagination, PageHeader

Use business-level composition only in Sarae Live Studio. Keep product and video logic out of the shared core.

## Required checks before enabling library components
- Ensure Sass is installed and declared in `web/package.json`.
- Ensure the source-level package's dependencies and type declarations resolve in the consuming app.
- Check for collisions between the library's global `:root` styles and Sarae theme before importing its SCSS.
- Support both modes of dark theme (current app uses `data-theme`, UI Core uses `data-ui-mode`).
- Confirm responsiveness and accessibility at phone, tablet and desktop sizes.
- Run `npm run build` and `npm run lint`.
- Pin a reviewed commit instead of floating a production dependency on `dev`.

Do not install all advanced rich-text/chat/media dependencies solely for a dashboard with placeholder pages. Integrate controls alongside actual business forms.
