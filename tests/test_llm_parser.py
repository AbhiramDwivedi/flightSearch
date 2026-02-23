import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from flight_search import config
from flight_search.llm_parser import _query_hash, _save_parse, _load_parse, parse_query
from flight_search.models import ParsedQuery

class TestLLMParser(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for the cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_cache = Path(self.temp_dir.name) / ".last_parse.json"
        
        # Patch the config to use our temp cache
        self.config_patcher = patch('flight_search.llm_parser.config.PARSED_CACHE_FILE', self.temp_cache)
        self.config_patcher.start()

        self.sample_query = "round trip from SFO to JFK"
        self.sample_parsed = ParsedQuery(
            query_summary="SFO to JFK",
            combinations=[],
            post_filters=[],
            ranking_preference="price"
        )

    def tearDown(self):
        self.config_patcher.stop()
        self.temp_dir.cleanup()

    def test_query_hash_consistency(self):
        hash1 = _query_hash("test query")
        hash2 = _query_hash("test query")
        hash3 = _query_hash("test query ") # Should strip whitespace
        self.assertEqual(hash1, hash2)
        self.assertEqual(hash1, hash3)
        self.assertNotEqual(hash1, _query_hash("different query"))

    def test_save_and_load_parse(self):
        # Initially empty
        self.assertIsNone(_load_parse(self.sample_query))

        # Save
        _save_parse(self.sample_parsed, self.sample_query)

        # Load with same query
        loaded = _load_parse(self.sample_query)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.query_summary, "SFO to JFK")

        # Load with different query (hash mismatch)
        self.assertIsNone(_load_parse("different query"))

    @patch('flight_search.llm_parser.OpenAI')
    def test_parse_query_uses_cache(self, mock_openai):
        # Save a cached version
        _save_parse(self.sample_parsed, self.sample_query)

        # Call parse_query
        result = parse_query(self.sample_query)

        # Should return cached version and NOT call OpenAI
        self.assertEqual(result.query_summary, "SFO to JFK")
        mock_openai.assert_not_called()

    @patch('flight_search.llm_parser.OpenAI')
    def test_parse_query_force_reparse(self, mock_openai):
        # Save a cached version
        _save_parse(self.sample_parsed, self.sample_query)

        # Setup mock OpenAI response
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_parsed = ParsedQuery(
            query_summary="New Parse",
            combinations=[],
            post_filters=[],
            ranking_preference="price"
        )
        mock_client.responses.parse.return_value = mock_response

        # Call parse_query with force=True
        result = parse_query(self.sample_query, force=True)

        # Should return new version and call OpenAI
        self.assertEqual(result.query_summary, "New Parse")
        mock_client.responses.parse.assert_called_once()

if __name__ == '__main__':
    unittest.main()
