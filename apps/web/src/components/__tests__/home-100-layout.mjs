// Start only this isolated Vite fixture when explicitly run; no production QA route.
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
const require=createRequire(import.meta.url);
const entry=require.resolve("vite",{paths:[require.resolve("vitest")]});
const {createServer}=await import(pathToFileURL(entry).href);
const root=resolve(dirname(fileURLToPath(import.meta.url)),"../../..");
const server=await createServer({root,configFile:resolve(root,"vitest.config.mts"),define:{"process.env":JSON.stringify({NODE_ENV:"development"})},server:{host:"127.0.0.1",port:4309,strictPort:true}});
await server.listen();
console.log("布局fixture（非生产数据） http://127.0.0.1:4309/src/components/__tests__/home-100-layout.html");
