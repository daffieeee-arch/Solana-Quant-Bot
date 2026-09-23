import React from 'react';
import { createRoot } from 'react-dom/client';
import { MintInspector } from './MintInspector';
import './inspector.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><MintInspector /></React.StrictMode>);
