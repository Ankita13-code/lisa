# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import cast

from lisa.executable import Tool
from lisa.operating_system import Posix


class Perl_CPAN(Tool):
    @property
    def command(self) -> str:
        return "perl-CPAN"

    @property
    def can_install(self) -> bool:
        return self.node.os.is_posix

    def install(self) -> bool:
        posix_os: Posix = cast(Posix, self.node.os)
        package_name = "perl-CPAN"
        posix_os.install_packages(package_name)
        return self._check_exists()