// Tiny RPC-over-stdio harness: reads one JSON line {"module": "...", "fn": "...", "args": [...]}
// from stdin, requires the compiled module (path via TS_BUILD_DIR env var,
// set by conftest.py after it tsc-compiles the real frontend/src/*.ts
// files), calls the named export with the given args, and prints the
// result as one JSON line on stdout.
//
// Kept deliberately dumb -- this is a test fixture, not a general RPC
// bridge. Its only job is letting Python hand the exact same input to the
// exact same TypeScript function the UI ships and get the answer back.

const buildDir = process.env.TS_BUILD_DIR;
if (!buildDir) {
  console.error("TS_BUILD_DIR not set");
  process.exit(1);
}

let input = "";
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  const { module: moduleName, fn, args } = JSON.parse(input);
  const mod = require(`${buildDir}/${moduleName}.js`);
  if (typeof mod[fn] !== "function") {
    console.error(`${moduleName}.${fn} is not a function (got ${typeof mod[fn]})`);
    process.exit(1);
  }
  const result = mod[fn](...args);
  process.stdout.write(JSON.stringify(result));
});
