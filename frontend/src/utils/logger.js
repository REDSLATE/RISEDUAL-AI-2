/** Production-safe logger — only outputs in development mode. */
const isDev = process.env.NODE_ENV === 'development';

const logger = {
  log: (...args) => isDev && console.log(...args),
  warn: (...args) => isDev && console.warn(...args),
  error: (...args) => isDev && console.error(...args),
  info: (...args) => isDev && console.info(...args),
};

export default logger;
