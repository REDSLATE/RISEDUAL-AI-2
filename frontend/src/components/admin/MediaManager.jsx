import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Upload, Trash2, Film, Image, Music, FileText, RefreshCw, CheckCircle, Loader2 } from 'lucide-react';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

const CATEGORY_OPTIONS = [
  { value: 'landing', label: 'Landing Page' },
  { value: 'commercial', label: 'Commercial' },
  { value: 'general', label: 'General' },
];

const typeIcon = (ct) => {
  if (!ct) return <FileText className="w-4 h-4" />;
  if (ct.startsWith('video/')) return <Film className="w-4 h-4 text-violet-400" />;
  if (ct.startsWith('image/')) return <Image className="w-4 h-4 text-teal-400" />;
  if (ct.startsWith('audio/')) return <Music className="w-4 h-4 text-amber-400" />;
  return <FileText className="w-4 h-4 text-slate-400" />;
};

const fmtSize = (bytes) => {
  if (!bytes) return '—';
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes > 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
};

const CHUNK_SIZE = 2 * 1024 * 1024; // 2MB chunks

const MediaManager = () => {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [category, setCategory] = useState('landing');
  const fileRef = useRef(null);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/media`);
      if (res.ok) {
        const data = await res.json();
        setFiles(data.files || []);
      }
    } catch (e) { logger.warn('Media fetch failed:', e); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchFiles(); }, [fetchFiles]);

  const uploadChunked = async (file) => {
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const uploadId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);

      const fd = new FormData();
      fd.append('file', chunk, `chunk_${i}`);
      fd.append('chunk_index', String(i));
      fd.append('total_chunks', String(totalChunks));
      fd.append('upload_id', uploadId);
      fd.append('filename', file.name);
      fd.append('category', category);
      fd.append('content_type', file.type || 'application/octet-stream');

      const res = await authFetch(`${API}/media/upload-chunk`, {
        method: 'POST',
        body: fd,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Chunk ${i + 1} failed`);
      }

      setProgress(Math.round(((i + 1) / totalChunks) * 100));
    }
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setProgress(0);

    try {
      if (file.size > 2 * 1024 * 1024) {
        await uploadChunked(file);
      } else {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('category', category);
        const res = await authFetch(`${API}/media/upload`, { method: 'POST', body: fd });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Upload failed');
        }
        setProgress(100);
      }
      toast.success(`Uploaded ${file.name}`);
      fetchFiles();
    } catch (err) {
      toast.error(err.message || 'Upload failed');
    } finally {
      setUploading(false);
      setProgress(0);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleDelete = async (fileId) => {
    try {
      const res = await authFetch(`${API}/media/${fileId}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success('Deleted');
        fetchFiles();
      } else {
        toast.error('Delete failed');
      }
    } catch { toast.error('Delete failed'); }
  };

  return (
    <div className="p-4 space-y-4" data-testid="media-manager">
      {/* Upload area */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="bg-slate-800 border border-slate-600 text-white text-sm rounded-lg px-3 py-2"
          data-testid="media-category-select"
        >
          {CATEGORY_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        <Button
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="bg-[#3DE8D9] hover:bg-[#67E3D3] text-slate-900 font-medium"
          data-testid="media-upload-btn"
        >
          {uploading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Upload className="w-4 h-4 mr-2" />}
          {uploading ? `Uploading ${progress}%` : 'Upload File'}
        </Button>

        <input ref={fileRef} type="file" className="hidden" onChange={handleUpload} accept="video/*,image/*,audio/*,.pdf" data-testid="media-file-input" />

        <Button variant="outline" size="sm" onClick={fetchFiles} className="bg-slate-800 border-slate-600 text-white ml-auto" data-testid="media-refresh-btn">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* Progress bar */}
      {uploading && (
        <div className="w-full bg-slate-700 rounded-full h-2">
          <div className="bg-[#3DE8D9] h-2 rounded-full transition-all duration-300" style={{ width: `${progress}%` }} />
        </div>
      )}

      {/* File list */}
      {loading ? (
        <div className="text-center text-slate-400 py-8">Loading...</div>
      ) : files.length === 0 ? (
        <div className="text-center text-slate-400 py-8">No media files uploaded yet.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="media-files-table">
            <thead>
              <tr className="border-b border-slate-600/40">
                <th className="text-left text-slate-300 text-xs font-medium px-3 py-2">Type</th>
                <th className="text-left text-slate-300 text-xs font-medium px-3 py-2">Filename</th>
                <th className="text-left text-slate-300 text-xs font-medium px-3 py-2">Category</th>
                <th className="text-left text-slate-300 text-xs font-medium px-3 py-2">Size</th>
                <th className="text-left text-slate-300 text-xs font-medium px-3 py-2">Date</th>
                <th className="text-right text-slate-300 text-xs font-medium px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {files.map(f => (
                <tr key={f.file_id} className="border-b border-slate-600/30 hover:bg-slate-700/35">
                  <td className="px-3 py-2">{typeIcon(f.content_type)}</td>
                  <td className="px-3 py-2 text-white max-w-[200px] truncate">{f.original_filename}</td>
                  <td className="px-3 py-2">
                    <Badge className="text-[10px] bg-slate-700 text-slate-300">{f.category}</Badge>
                  </td>
                  <td className="px-3 py-2 text-slate-300">{fmtSize(f.size)}</td>
                  <td className="px-3 py-2 text-slate-400 text-xs">{f.created_at?.slice(0, 10) || '—'}</td>
                  <td className="px-3 py-2 text-right flex items-center justify-end gap-2">
                    <a
                      href={`${API}/media/file/${f.file_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[#3DE8D9] hover:text-[#67E3D3] text-xs underline"
                      data-testid={`media-view-${f.file_id}`}
                    >
                      View
                    </a>
                    <button
                      onClick={() => handleDelete(f.file_id)}
                      className="text-red-400 hover:text-red-300"
                      data-testid={`media-delete-${f.file_id}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default MediaManager;
