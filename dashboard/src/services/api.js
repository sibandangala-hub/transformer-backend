const BASE_URL = 'https://transformer-pm-api.onrender.com';
const API_KEY  = 'your_secret_key_123';

const headers = {
  'Content-Type': 'application/json',
  'x-api-key':    API_KEY
};

export async function fetchHealth() {
  try {
    const res = await fetch(`${BASE_URL}/health`);
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchAlerts() {
  try {
    const res = await fetch(`${BASE_URL}/api/alerts`, { headers });
    return await res.json();
  } catch {
    return { alerts: [] };
  }
}

export async function acknowledgeAlert(id) {
  await fetch(`${BASE_URL}/api/alerts/${id}/ack`, {
    method: 'PATCH', headers
  });
}