const fs = require('fs');

// Lies die Task-Datei
const taskData = JSON.parse(fs.readFileSync('task.json', 'utf8'));

// Sende per fetch API
fetch('http://localhost:3000/api/tasks', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify(taskData)
})
.then(response => response.json())
.then(data => console.log('Task gestartet:', data))
.catch(error => console.error('Fehler:', error));