import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Stock Market APIs
export const getTickerData = async () => {
  try {
    const response = await axios.get(`${API}/stocks/ticker`);
    return response.data;
  } catch (error) {
    console.error('Error fetching ticker data:', error);
    throw error;
  }
};

export const getStockQuote = async (symbol) => {
  try {
    const response = await axios.get(`${API}/stocks/quote/${symbol}`);
    return response.data;
  } catch (error) {
    console.error(`Error fetching quote for ${symbol}:`, error);
    throw error;
  }
};

// Options APIs
export const getOptionsRadar = async () => {
  try {
    const response = await axios.get(`${API}/options/radar`);
    return response.data;
  } catch (error) {
    console.error('Error fetching options radar:', error);
    throw error;
  }
};

export const getOptionsFlow = async () => {
  try {
    const response = await axios.get(`${API}/options/flow`);
    return response.data;
  } catch (error) {
    console.error('Error fetching options flow:', error);
    throw error;
  }
};

export const getMomentumData = async () => {
  try {
    const response = await axios.get(`${API}/options/momentum`);
    return response.data;
  } catch (error) {
    console.error('Error fetching momentum data:', error);
    throw error;
  }
};

export const getFastMovers = async () => {
  try {
    const response = await axios.get(`${API}/options/fast-movers`);
    return response.data;
  } catch (error) {
    console.error('Error fetching fast movers:', error);
    throw error;
  }
};

export const getUnusualVolume = async () => {
  try {
    const response = await axios.get(`${API}/options/unusual-volume`);
    return response.data;
  } catch (error) {
    console.error('Error fetching unusual volume:', error);
    throw error;
  }
};

// Crypto APIs
export const getCryptoPrices = async () => {
  try {
    const response = await axios.get(`${API}/crypto/prices`);
    return response.data;
  } catch (error) {
    console.error('Error fetching crypto prices:', error);
    throw error;
  }
};

export const getCryptoBySymbol = async (symbol) => {
  try {
    const response = await axios.get(`${API}/crypto/${symbol}`);
    return response.data;
  } catch (error) {
    console.error(`Error fetching crypto ${symbol}:`, error);
    throw error;
  }
};

// Dark Pool APIs
export const getDarkPoolData = async () => {
  try {
    const response = await axios.get(`${API}/dark-pool`);
    return response.data;
  } catch (error) {
    console.error('Error fetching dark pool data:', error);
    throw error;
  }
};

// AI Chat APIs
export const sendChatMessage = async (message, sessionId, imageBase64 = null) => {
  try {
    const payload = { message, sessionId };
    if (imageBase64) {
      payload.image_base64 = imageBase64;
    }
    const response = await axios.post(`${API}/chat`, payload);
    return response.data;
  } catch (error) {
    console.error('Error sending chat message:', error);
    throw error;
  }
};

export const getChatHistory = async (sessionId) => {
  try {
    const response = await axios.get(`${API}/chat/history/${sessionId}`);
    return response.data;
  } catch (error) {
    console.error('Error fetching chat history:', error);
    throw error;
  }
};
