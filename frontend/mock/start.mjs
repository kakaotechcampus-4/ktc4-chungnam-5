// mock 서버 시작: 고정 응답을 끼운 스펙을 만들고 Prism 을 띄운다(`npm start`).
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

await import('./build-spec.mjs');

const prism = spawn(
  'npx -y @stoplight/prism-cli@5.16.0 mock -h 0.0.0.0 -p 4010 .build/openapi.mock.json',
  {
    cwd: fileURLToPath(new URL('.', import.meta.url)),
    // 절대 경로는 한글·공백이 섞이면 NODE_OPTIONS 가 못 읽는다. cwd 기준 상대 경로로 준다.
    // 이미 쓰는 NODE_OPTIONS 가 있으면 뒤에 이어 붙인다.
    env: {
      ...process.env,
      NODE_OPTIONS: [process.env.NODE_OPTIONS, '--require ./patch-headers.cjs']
        .filter(Boolean)
        .join(' '),
    },
    shell: true,
    stdio: 'inherit',
  },
);
prism.on('exit', (code) => process.exit(code ?? 0));
