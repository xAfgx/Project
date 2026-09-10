const http = require('http');

const data = JSON.stringify({
  "id": "local-test-1",
  "config": {
    "shopId": "pokemon-center-de",
    "data": {
      "profileId": "default-profile",
      "postQueueDiscovery": {
        "productName": "pikachu",
        "keywords": ["pikachu"]
      }
    }
  }
});

const options = {
  hostname: 'localhost',
  port: 3000,
  path: '/api/tasks',
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Content-Length': data.length
  }
};

const req = http.request(options, res => {
  console.log(`Status: ${res.statusCode}`);
  res.on('data', d => {
    process.stdout.write(d);
  });
});

req.on('error', error => {
  console.error('Fehler:', error);
});

req.write(data);
req.end();