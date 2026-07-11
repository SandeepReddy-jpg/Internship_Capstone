const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const uploadBtn = document.getElementById('uploadBtn');
const status = document.getElementById('status');
let selectedFile = null;

dropzone.addEventListener('click', () => fileInput.click());

dropzone.addEventListener('dragover', e => {
  e.preventDefault();
  dropzone.classList.add('dragover');
});

dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));

dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  handleFile(e.dataTransfer.files[0]);
});

fileInput.addEventListener('change', e => handleFile(e.target.files[0]));

function handleFile(file) {
  if (!file || !file.name.endsWith('.csv')) {
    status.textContent = '⚠️ Please select a valid CSV file.';
    return;
  }
  selectedFile = file;
  dropzone.querySelector('p').textContent = `Selected: ${file.name}`;
  uploadBtn.disabled = false;
  status.textContent = '';
}

uploadBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  uploadBtn.disabled = true;
  status.textContent = '⏳ Uploading and analyzing reviews...';

  const formData = new FormData();
  formData.append('file', selectedFile);

  try {
    const res = await fetch('/upload', { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) {
      status.textContent = '❌ Error: ' + (data.error || 'Upload failed.');
      uploadBtn.disabled = false;
      return;
    }

    status.textContent = `✅ Processed ${data.processed_count} reviews successfully.`;
    renderSummary(data.results);
    document.getElementById('summary').style.display = 'block';
    document.getElementById('powerbiActions').classList.remove('hidden');

  } catch (err) {
    status.textContent = '❌ Network error: ' + err.message;
  }
  uploadBtn.disabled = false;
});

function renderSummary(results) {
  let pos = 0, neg = 0, neu = 0;
  const tbody = document.querySelector('#resultsTable tbody');
  tbody.innerHTML = '';

  results.forEach(r => {
    const label = (r.predicted_sentiment || '').toLowerCase();
    if (label.includes('pos')) pos++;
    else if (label.includes('neg')) neg++;
    else neu++;

    const row = document.createElement('tr');
    row.innerHTML = `<td>${r.review.substring(0, 80)}${r.review.length > 80 ? '...' : ''}</td>
                      <td>${r.predicted_sentiment}</td>
                      <td>${(r.confidence_score * 100).toFixed(1)}%</td>`;
    tbody.appendChild(row);
  });

  document.getElementById('totalCount').textContent = results.length;
  document.getElementById('posCount').textContent = pos;
  document.getElementById('negCount').textContent = neg;
  document.getElementById('neuCount').textContent = neu;
}