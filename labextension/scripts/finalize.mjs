import fs from 'node:fs';

for (const filename of ['LICENSE', 'daslang-LICENSE.txt', 'install.json']) {
  fs.copyFileSync(filename, `labextension/${filename}`);
}
const file = 'labextension/package.json';
const data = JSON.parse(fs.readFileSync(file, 'utf8'));
data.jupyterlab._build.load = data.jupyterlab._build.load.replaceAll('\\', '/');
fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
