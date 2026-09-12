const http = require('node:http');
const fs = require('node:fs');
const files = { '/': ['index.html', 'text/html'], '/fixture.js': ['fixture.js', 'text/javascript'], '/fixture.css': ['fixture.css', 'text/css'] };
http.createServer((req, res) => {
  const file = files[req.url];
  if (!file) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file[1]);
  res.end(fs.readFileSync('/private/tmp/cuevion-c3p2a/' + file[0]));
}).listen(4175, '127.0.0.1', () => console.log('Local fixture: http://127.0.0.1:4175'));
