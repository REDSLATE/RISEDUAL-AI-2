// Mock data for RISEDUAL AI - will be replaced with real API data

export const stockTickerData = [
  { symbol: 'SPY', price: 572.38, change: -8.93, changePercent: -1.31 },
  { symbol: 'VOO', price: 618.43, change: -8.38, changePercent: -1.34 },
  { symbol: 'QQQ', price: 599.75, change: -9.16, changePercent: -1.50 },
  { symbol: 'IVV', price: 675.40, change: -9.06, changePercent: -1.32 },
  { symbol: 'VTI', price: 331.41, change: -4.59, changePercent: -1.37 },
  { symbol: 'VUG', price: 458.08, change: -6.23, changePercent: -1.34 },
  { symbol: 'VEA', price: 65.28, change: -0.51, changePercent: -0.78 },
];

export const optionsRadarData = {
  mostActivelyTraded: [
    { contract: 'CORZ', price: '$16 Put', returns: '167%', volOI: 0.14, power: 91, ivRank: 30, aiScore: 33 },
    { contract: 'BBAI', price: '$4 Call', returns: '164%', volOI: 1.09, power: 47, ivRank: 25, aiScore: 36 },
    { contract: 'BX', price: '$115 Put', returns: '136%', volOI: 0.22, power: 66, ivRank: 48, aiScore: 27 },
    { contract: 'PLTR', price: '$150 Call', returns: '74%', volOI: 0.77, power: 83, ivRank: 21, aiScore: 38 },
  ],
  volatilityOpportunities: [
    { contract: 'T', price: '$29 Call', ivRank: 27, returns: '59%', power: 98, volOI: 2.61, aiScore: 52 },
    { contract: 'QQQ', price: '$634 Put', ivRank: 50, returns: '24%', power: 85, volOI: 1.95, aiScore: 37 },
    { contract: 'IEP', price: '$75 Call', ivRank: 29, returns: '20%', power: 97, volOI: 0.05, aiScore: 37 },
    { contract: 'VZ', price: '$50 Call', ivRank: 46, returns: '18%', power: 95, volOI: 0.06, aiScore: 50 },
  ]
};

export const optionsFlowData = {
  mostActivelyTraded: [
    { contract: 'MRVL', price: '$95 Call', returns: '309%', volOI: 8.37, power: 60, ivRank: 69, aiScore: 34 },
    { contract: 'AMZN', price: '$2175 Put', returns: '282%', volOI: 0.94, power: 90, ivRank: 28, aiScore: 40 },
    { contract: 'META', price: '$655 Put', returns: '257%', volOI: 0.46, power: 58, ivRank: 21, aiScore: 40 },
    { contract: 'PLTR', price: '$170 Call', returns: '217%', volOI: 3.11, power: 67, ivRank: 21, aiScore: 38 },
    { contract: 'NVDA', price: '$175 Put', returns: '189%', volOI: 7.62, power: 80, ivRank: 38, aiScore: 35 },
  ],
  dteEdge: [
    { contract: 'USO', price: '$100 Call', power: 89, returns: '1134%', volOI: 0.83, aiScore: 47, ivRank: 50 },
    { contract: 'MRVL', price: '$85 Call', power: 61, returns: '384%', volOI: 1.01, aiScore: 34, ivRank: 69 },
    { contract: 'LITE', price: '$6175 Put', power: 59, returns: '341%', volOI: 2.61, aiScore: 47, ivRank: 84 },
    { contract: 'PLTR', price: '$155 Call', power: 57, returns: '310%', volOI: 5.36, aiScore: 38, ivRank: 21 },
    { contract: 'NVDA', price: '$180 Put', power: 73, returns: '247%', volOI: 7.44, aiScore: 35, ivRank: 38 },
  ],
  volatilityLow: [
    { contract: 'BAC', price: '$48 Put', ivRank: 21, returns: '151%', power: 93, volOI: 0.04, aiScore: 37 },
    { contract: 'DGX', price: '$200 Put', ivRank: 41, returns: '71%', power: 100, volOI: 0.25, aiScore: 51 },
    { contract: 'RTX', price: '$220 Call', ivRank: 63, returns: '64%', power: 98, volOI: 0.35, aiScore: 66 },
  ],
  volatilityHigh: [
    { contract: 'SWCE', price: '$20 Call', ivRank: 21, returns: '544%', power: 98, volOI: 2.60, aiScore: 39 },
    { contract: 'MU', price: '$290 Put', ivRank: 97, returns: '82%', power: 100, volOI: 98.08, aiScore: 46 },
    { contract: 'APLD', price: '$22 Put', ivRank: 45, returns: '93%', power: 94, volOI: 0.35, aiScore: 39 },
  ]
};

export const momentumData = [
  { contract: 'MRVL', price: '$95 Call', returns: '300%', power: 66, volOI: 1.03, ivRank: 69, aiScore: 34 },
  { contract: 'CF', price: '$125 Call', returns: '146%', power: 73, volOI: 32.73, ivRank: 86, aiScore: 60 },
  { contract: 'PLTR', price: '$170 Call', returns: '85%', power: 84, volOI: 3.45, ivRank: 21, aiScore: 38 },
  { contract: 'AMPX', price: '$20 Call', returns: '85%', power: 70, volOI: 2.36, ivRank: 35, aiScore: 66 },
  { contract: 'ASPI', price: '$55 Call', returns: '61%', power: 85, volOI: 51.66, ivRank: 39, aiScore: 34 },
];

export const fastMoverCallsData = [
  { contract: 'BA', price: '$240 Call', returns: '364%', volOI: 5.64, ivRank: 25, power: 24, aiScore: 39 },
  { contract: 'BLK', price: '$960 Put', returns: '296%', volOI: 9.35, ivRank: 50, power: 76, aiScore: 36 },
  { contract: 'SNDK', price: '$400 Put', returns: '280%', volOI: 2.27, ivRank: 62, power: 52, aiScore: 48 },
  { contract: 'MRVL', price: '$80 Call', returns: '266%', volOI: 0.31, ivRank: 69, power: 77, aiScore: 34 },
  { contract: 'MU', price: '$280 Put', returns: '223%', volOI: 1.64, ivRank: 97, power: 86, aiScore: 46 },
];

export const unusualVolumeData = [
  { contract: 'GLW', price: '$118 Put', volOI: 39.62, returns: '142%', power: 94, sentiment: 'Bearish', aiScore: 58 },
  { contract: 'META', price: '$6.475 Put', volOI: 9.54, returns: '99%', power: 54, sentiment: 'Bearish', aiScore: 40 },
  { contract: 'SNDK', price: '$5.375 Put', volOI: 14.64, returns: '78%', power: 98, sentiment: 'Bearish', aiScore: 48 },
];