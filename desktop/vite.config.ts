import { defineConfig } from 'vite';import react from '@vitejs/plugin-react';
// Vite watches its whole root (desktop/). On Windows, chokidar then tries to watch build-script .exe files
// that Cargo is writing under src-tauri/target and fails with EBUSY, killing `tauri dev`. Only src/ and
// index.html are frontend inputs, so skip the Rust crate, build outputs and the legacy Flutter workspace.
const root=decodeURIComponent(new URL('.',import.meta.url).pathname).replace(/^\/(?=[A-Za-z]:)/,'');
const ignored=['**/src-tauri/**','**/dist/**',...['build','windows','lib','test','web','docs','.dart_tool','.idea'].map(dir=>`${root}${dir}/**`)];
export default defineConfig({plugins:[react()],clearScreen:false,server:{host:'127.0.0.1',port:1420,strictPort:true,watch:{ignored}},envPrefix:['VITE_','TAURI_ENV_*']});
