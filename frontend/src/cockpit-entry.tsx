import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ResearchCockpit } from './research/ResearchCockpit.js';
import './cockpit.css';

const root = document.getElementById('root');
if (!root) throw new Error('cockpit_root_missing');

createRoot(root).render(<StrictMode><ResearchCockpit /></StrictMode>);
