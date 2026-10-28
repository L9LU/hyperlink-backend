import apiClient from './client';

export const linkPatient = async (linkData) => {
  const response = await apiClient.post('/doctor/link-patient', linkData);
  return response.data;
};

export const getDoctorPatients = async (doctorId) => {
  const response = await apiClient.get(`/doctor/patients?doctor_id=${doctorId}`);
  return response.data;
};

export const getPatientDetail = async (patientId, doctorId) => {
  const response = await apiClient.get(`/doctor/patient/${patientId}?doctor_id=${doctorId}`);
  return response.data;
};

export const markFollowupDone = async ({ doctor_id, patient_id, note }) => {
  const response = await apiClient.post('/doctor/followup-done', { doctor_id, patient_id, note });
  return response.data;
};

export const reviewReading = async ({ doctor_id, patient_id, reading_id, outcome }) => {
  const response = await apiClient.post('/doctor/review-reading', {
    doctor_id,
    patient_id,
    reading_id,
    outcome,
  });
  return response.data;
};

// ── Doctor-initiated linking: the doctor searches, sends a request, the patient accepts ──

// Find a patient by their exact phone number or email.
export const searchPatient = async (query) => {
  const response = await apiClient.post('/doctor/search-patient', { query });
  return response.data;
};

export const sendLinkRequest = async (patientId) => {
  const response = await apiClient.post('/doctor/link-requests', { patient_id: patientId });
  return response.data;
};

export const getDoctorLinkRequests = async () => {
  const response = await apiClient.get('/doctor/link-requests');
  return response.data;
};

export const cancelLinkRequest = async (requestId) => {
  const response = await apiClient.post(`/doctor/link-requests/${requestId}/cancel`);
  return response.data;
};

// Patient side (used by the patient screens)
export const getPatientLinkRequests = async () => {
  const response = await apiClient.get('/patient/link-requests');
  return response.data;
};

export const respondToLinkRequest = async (requestId, accept) => {
  const response = await apiClient.post(`/patient/link-requests/${requestId}/respond`, { accept });
  return response.data;
};

export const unlinkDoctor = async () => {
  const response = await apiClient.post('/patient/unlink-doctor');
  return response.data;
};
