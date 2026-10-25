import next from "eslint-config-next/core-web-vitals";
import ts from "eslint-config-next/typescript";

const config = [...next, ...ts, { ignores: [".next/**", "node_modules/**", "public/sw.js"] }];

export default config;
