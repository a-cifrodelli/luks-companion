"""
Tests for privilege dropping utility.
"""
from unittest.mock import patch, MagicMock
from luks_companion.core.privdrop import drop_privileges, resolve_user_group


def test_drop_privileges_when_already_non_root():
    # If os.getuid() != 0, it should return False without attempting privilege drops
    with patch("os.getuid", create=True, return_value=1000):
        with patch("os.setuid", create=True) as mock_setuid:
            res = drop_privileges("luks-web")
            assert res is False
            mock_setuid.assert_not_called()


def test_drop_privileges_sequence_when_root():
    # When root, verify setgroups -> setgid -> setuid sequence
    with patch("os.getuid", create=True, side_effect=[0, 1001]):
        with patch("os.getgid", create=True, return_value=1002):
            with patch("os.setgroups", create=True) as mock_setgroups:
                with patch("os.setgid", create=True) as mock_setgid:
                    with patch("os.setuid", create=True) as mock_setuid:
                        with patch(
                            "luks_companion.core.privdrop.resolve_user_group",
                            return_value=(1001, 1002),
                        ):
                            res = drop_privileges("luks-web", "luks-web")
                            assert res is True
                            mock_setgroups.assert_called_once_with([])
                            mock_setgid.assert_called_once_with(1002)
                            mock_setuid.assert_called_once_with(1001)
