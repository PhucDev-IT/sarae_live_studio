// Import only the components that Sarae Live Studio actually uses.
// Avoid the UI Core root barrel: it eagerly traverses rich text, charts,
// uploads and their JSX types while this application needs only basics.
export { Button } from '../../../vendor/react-ui-core/packages/ui/src/components/Button/Button'
export { Input } from '../../../vendor/react-ui-core/packages/ui/src/components/Input/Input'
export { ThemeProvider } from '../../../vendor/react-ui-core/packages/ui/src/theme/ThemeProvider'
