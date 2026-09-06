import React from 'react';
import { createRoot } from 'react-dom/client';
import { AcquisitionMonitor } from './AcquisitionMonitor';
import './monitor.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AcquisitionMonitor /></React.StrictMode>,
);
