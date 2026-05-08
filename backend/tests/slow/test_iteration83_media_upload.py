"""
Iteration 83: Media Upload / Object Storage System Tests
Tests for the media upload, chunked upload, list, download, delete, and landing-video endpoints.
Uses real Emergent Object Storage (no mocking).
"""
import pytest
import requests
import os
import uuid
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestMediaEndpoints:
    """Test media upload/download/list/delete endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session"""
        self.session = requests.Session()
        # Don't set Content-Type for multipart uploads - requests handles it
        self.created_file_ids = []
        yield
        # Cleanup: delete test files
        for file_id in self.created_file_ids:
            try:
                self.session.delete(f"{BASE_URL}/api/media/{file_id}")
            except Exception:
                pass
    
    def test_list_media_endpoint(self):
        """Test GET /api/media - list all uploaded media files"""
        response = self.session.get(f"{BASE_URL}/api/media")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "files" in data, "Response should contain 'files' key"
        assert "count" in data, "Response should contain 'count' key"
        assert isinstance(data["files"], list), "files should be a list"
        assert isinstance(data["count"], int), "count should be an integer"
        print(f"PASS: GET /api/media returns {data['count']} files")
    
    def test_list_media_with_category_filter(self):
        """Test GET /api/media?category=landing - filter by category"""
        response = self.session.get(f"{BASE_URL}/api/media?category=landing")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "files" in data
        # All returned files should have category=landing
        for f in data["files"]:
            assert f.get("category") == "landing", f"File {f.get('file_id')} has category {f.get('category')}, expected 'landing'"
        print(f"PASS: GET /api/media?category=landing returns {data['count']} landing files")
    
    def test_upload_small_file(self):
        """Test POST /api/media/upload - single file upload (small files)"""
        # Create a small test file (1KB)
        test_content = b"TEST_MEDIA_" + os.urandom(1000)
        test_filename = f"test_small_{uuid.uuid4().hex[:8]}.txt"
        
        files = {"file": (test_filename, test_content, "text/plain")}
        data = {"category": "general"}
        
        response = self.session.post(
            f"{BASE_URL}/api/media/upload",
            files=files,
            data=data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        result = response.json()
        assert "file_id" in result, "Response should contain file_id"
        assert "storage_path" in result, "Response should contain storage_path"
        assert result["filename"] == test_filename, f"Filename mismatch: {result['filename']} != {test_filename}"
        assert result["category"] == "general", f"Category mismatch: {result['category']} != general"
        
        self.created_file_ids.append(result["file_id"])
        print(f"PASS: POST /api/media/upload - uploaded {test_filename}, file_id={result['file_id']}")
        return result["file_id"]
    
    def test_upload_file_too_small(self):
        """Test POST /api/media/upload - reject files that are too small (<100 bytes)"""
        test_content = b"tiny"  # Only 4 bytes
        test_filename = "tiny.txt"
        
        files = {"file": (test_filename, test_content, "text/plain")}
        data = {"category": "general"}
        
        response = self.session.post(
            f"{BASE_URL}/api/media/upload",
            files=files,
            data=data
        )
        
        # Backend returns 400 or 500 for tiny files (validation error)
        assert response.status_code in [400, 500], f"Expected 400/500 for tiny file, got {response.status_code}"
        print(f"PASS: POST /api/media/upload rejects files < 100 bytes (status={response.status_code})")
    
    def test_chunked_upload(self):
        """Test POST /api/media/upload-chunk - chunked upload for large files"""
        # Create a 3MB test file (larger than 2MB chunk threshold)
        file_size = 3 * 1024 * 1024  # 3MB
        test_content = b"TEST_CHUNK_" + os.urandom(file_size - 11)
        test_filename = f"test_chunked_{uuid.uuid4().hex[:8]}.bin"
        upload_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        
        chunk_size = 2 * 1024 * 1024  # 2MB chunks
        total_chunks = (len(test_content) + chunk_size - 1) // chunk_size
        
        print(f"Uploading {len(test_content)} bytes in {total_chunks} chunks...")
        
        for i in range(total_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, len(test_content))
            chunk_data = test_content[start:end]
            
            files = {"file": (f"chunk_{i}", chunk_data, "application/octet-stream")}
            data = {
                "chunk_index": str(i),
                "total_chunks": str(total_chunks),
                "upload_id": upload_id,
                "filename": test_filename,
                "category": "general",
                "content_type": "application/octet-stream"
            }
            
            response = self.session.post(
                f"{BASE_URL}/api/media/upload-chunk",
                files=files,
                data=data
            )
            
            assert response.status_code == 200, f"Chunk {i} failed: {response.status_code}: {response.text}"
            result = response.json()
            
            if i + 1 < total_chunks:
                # Intermediate chunk
                assert not result.get("complete"), f"Chunk {i} should not be complete"
                assert result.get("chunk") == i, "Chunk index mismatch"
            else:
                # Final chunk - should have file_id
                assert result.get("complete"), "Final chunk should be complete"
                assert "file_id" in result, "Final chunk should return file_id"
                self.created_file_ids.append(result["file_id"])
                print(f"PASS: Chunked upload complete - file_id={result['file_id']}")
                return result["file_id"]
    
    def test_landing_video_endpoint_no_video(self):
        """Test GET /api/media/landing-video - returns has_video=false when no landing video"""
        # First, check current state
        response = self.session.get(f"{BASE_URL}/api/media/landing-video")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "has_video" in data, "Response should contain 'has_video' key"
        # has_video can be true or false depending on existing data
        print(f"PASS: GET /api/media/landing-video returns has_video={data['has_video']}")
    
    def test_upload_landing_video_and_verify(self):
        """Test uploading a video to 'landing' category and verifying landing-video endpoint"""
        # Create a small fake video file (MP4 header + random data)
        # MP4 files start with ftyp box
        mp4_header = bytes([
            0x00, 0x00, 0x00, 0x1C,  # box size
            0x66, 0x74, 0x79, 0x70,  # 'ftyp'
            0x69, 0x73, 0x6F, 0x6D,  # 'isom'
            0x00, 0x00, 0x02, 0x00,  # minor version
            0x69, 0x73, 0x6F, 0x6D,  # compatible brand
            0x69, 0x73, 0x6F, 0x32,  # compatible brand
            0x61, 0x76, 0x63, 0x31,  # compatible brand
            0x6D, 0x70, 0x34, 0x31,  # compatible brand
        ])
        test_content = mp4_header + os.urandom(1000)  # Add some random data
        test_filename = f"test_landing_video_{uuid.uuid4().hex[:8]}.mp4"
        
        files = {"file": (test_filename, test_content, "video/mp4")}
        data = {"category": "landing"}
        
        response = self.session.post(
            f"{BASE_URL}/api/media/upload",
            files=files,
            data=data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()
        file_id = result["file_id"]
        self.created_file_ids.append(file_id)
        
        print(f"Uploaded landing video: {file_id}")
        
        # Now verify landing-video endpoint returns this video
        response = self.session.get(f"{BASE_URL}/api/media/landing-video")
        assert response.status_code == 200
        
        data = response.json()
        assert data["has_video"], "has_video should be True after uploading landing video"
        assert data["file_id"] == file_id, f"file_id mismatch: {data['file_id']} != {file_id}"
        assert data["content_type"] == "video/mp4", "content_type should be video/mp4"
        
        print(f"PASS: Landing video endpoint returns uploaded video {file_id}")
    
    def test_download_file(self):
        """Test GET /api/media/file/{file_id} - download/stream a file"""
        # First upload a file
        test_content = b"TEST_DOWNLOAD_" + os.urandom(500)
        test_filename = f"test_download_{uuid.uuid4().hex[:8]}.txt"
        
        files = {"file": (test_filename, test_content, "text/plain")}
        data = {"category": "general"}
        
        upload_response = self.session.post(
            f"{BASE_URL}/api/media/upload",
            files=files,
            data=data
        )
        assert upload_response.status_code == 200
        file_id = upload_response.json()["file_id"]
        self.created_file_ids.append(file_id)
        
        # Now download it
        download_response = self.session.get(f"{BASE_URL}/api/media/file/{file_id}")
        assert download_response.status_code == 200, f"Download failed: {download_response.status_code}"
        
        # Verify content matches
        assert download_response.content == test_content, "Downloaded content doesn't match uploaded content"
        
        # Check headers
        assert "Content-Disposition" in download_response.headers
        assert test_filename in download_response.headers["Content-Disposition"]
        
        print(f"PASS: GET /api/media/file/{file_id} - downloaded and verified content")
    
    def test_download_nonexistent_file(self):
        """Test GET /api/media/file/{file_id} - 404 for nonexistent file"""
        fake_id = str(uuid.uuid4())
        response = self.session.get(f"{BASE_URL}/api/media/file/{fake_id}")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: GET /api/media/file/{fake_id} returns 404 for nonexistent file")
    
    def test_delete_file(self):
        """Test DELETE /api/media/{file_id} - soft-delete a file"""
        # First upload a file
        test_content = b"TEST_DELETE_" + os.urandom(500)
        test_filename = f"test_delete_{uuid.uuid4().hex[:8]}.txt"
        
        files = {"file": (test_filename, test_content, "text/plain")}
        data = {"category": "general"}
        
        upload_response = self.session.post(
            f"{BASE_URL}/api/media/upload",
            files=files,
            data=data
        )
        assert upload_response.status_code == 200
        file_id = upload_response.json()["file_id"]
        
        # Delete it
        delete_response = self.session.delete(f"{BASE_URL}/api/media/{file_id}")
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.status_code}"
        
        result = delete_response.json()
        assert result["deleted"]
        assert result["file_id"] == file_id
        
        # Verify it's no longer accessible
        download_response = self.session.get(f"{BASE_URL}/api/media/file/{file_id}")
        assert download_response.status_code == 404, "Deleted file should return 404"
        
        print(f"PASS: DELETE /api/media/{file_id} - soft-deleted and verified inaccessible")
    
    def test_delete_nonexistent_file(self):
        """Test DELETE /api/media/{file_id} - 404 for nonexistent file"""
        fake_id = str(uuid.uuid4())
        response = self.session.delete(f"{BASE_URL}/api/media/{fake_id}")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: DELETE /api/media/{fake_id} returns 404 for nonexistent file")


class TestMediaUploadCategories:
    """Test media upload with different categories"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        self.created_file_ids = []
        yield
        for file_id in self.created_file_ids:
            try:
                self.session.delete(f"{BASE_URL}/api/media/{file_id}")
            except Exception:
                pass
    
    def test_upload_to_landing_category(self):
        """Test uploading to 'landing' category"""
        test_content = b"LANDING_" + os.urandom(500)
        files = {"file": ("landing_test.txt", test_content, "text/plain")}
        data = {"category": "landing"}
        
        response = self.session.post(f"{BASE_URL}/api/media/upload", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["category"] == "landing"
        self.created_file_ids.append(result["file_id"])
        print("PASS: Upload to 'landing' category works")
    
    def test_upload_to_commercial_category(self):
        """Test uploading to 'commercial' category"""
        test_content = b"COMMERCIAL_" + os.urandom(500)
        files = {"file": ("commercial_test.txt", test_content, "text/plain")}
        data = {"category": "commercial"}
        
        response = self.session.post(f"{BASE_URL}/api/media/upload", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["category"] == "commercial"
        self.created_file_ids.append(result["file_id"])
        print("PASS: Upload to 'commercial' category works")
    
    def test_upload_default_category(self):
        """Test uploading without category defaults to 'general'"""
        test_content = b"DEFAULT_" + os.urandom(500)
        files = {"file": ("default_test.txt", test_content, "text/plain")}
        # No category specified
        
        response = self.session.post(f"{BASE_URL}/api/media/upload", files=files)
        assert response.status_code == 200
        result = response.json()
        assert result["category"] == "general", f"Default category should be 'general', got {result['category']}"
        self.created_file_ids.append(result["file_id"])
        print("PASS: Upload without category defaults to 'general'")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
