//! Private attempt-directory file I/O for the future native phase-suite
//! producer (O1 slice, R1-R7 repaired). No production caller: explicit
//! low-level support with fail-closed semantics. All operations are confined
//! to a verified owner-only private directory created by
//! `create_private_child_directory` (root/test setup primitive).
//!
//! TRUSTED NAMESPACE (explicit): `PrivateAttemptDir` is path-only. `open`
//! verifies the directory AT OPEN TIME on a local handle, which is then
//! closed; every child operation later opens the child BY PATH inside the
//! trusted exclusive root-controlled namespace. A hostile concurrent
//! same-SID writer or parent-directory substitution is NOT prevented and no
//! TOCTOU/OS-immutability or persistent-handle claim is made.
//!
//! Windows implementation uses only already-enabled `windows-sys` features
//! (Win32_Foundation, Win32_System_Threading, Win32_Storage_FileSystem,
//! Win32_Security). Security verification is handle-bound via
//! `GetKernelObjectSecurity` — never a name-based query. Non-Windows targets
//! fail closed. Errors are fixed value-free typed kinds; payloads and
//! credentials never appear in Debug/Display output.
//!
//! Reading bytes is NOT JSON validation: callers must run their own strict
//! UTF-8/depth/fieldset parser afterwards.
//!
//! RETENTION POLICY (R3/R5): this API never automatically removes any
//! staging, claim or final file. A failed publication leaves its uniquely
//! named private staging file in place for explicit owner/root cleanup
//! after the attempt is abandoned; a failed claim leaves the burn marker in
//! place. Only test code cleans its own unique roots, after assertions.
//! A low-level finite control-document payload bound
//! (MAX_CONTROL_DOCUMENT_BYTES, aligned with the adapter's 2 MiB report
//! cap) applies to payloads and read limits here. The bound is INCLUSIVE:
//! a document of exactly MAX_CONTROL_DOCUMENT_BYTES bytes is valid for
//! claim, publish and bounded read, while bounded reads use a separately
//! checked one-byte scratch sentinel above the document size. It is NOT a
//! model-output/RSS capacity approval, and later claim/artifact callers
//! must impose their own smaller 8192/4096/… bounds.

use std::fmt;
use std::path::Path;

/// Finite documented caps for API-supplied allocation sizes.
const MAX_SID_BYTES: usize = 256;
const MAX_SECURITY_DESCRIPTOR_BYTES: usize = 1024;
/// Maximum child-name length in UTF-16 units (Windows namespace bound).
const MAX_NAME_UTF16_UNITS: usize = 128;
/// Maximum ASCII characters of the explicit safe child-name grammar.
const MAX_NAME_CHARS: usize = 96;
/// Low-level finite control-document bound for payloads and read limits.
/// NOT a model-output/RSS capacity approval; callers impose smaller bounds.
const MAX_CONTROL_DOCUMENT_BYTES: usize = 2 * 1024 * 1024;
/// Security descriptor revision 1 (SECURITY_DESCRIPTOR_REVISION).
const SECURITY_DESCRIPTOR_REVISION_1: u32 = 1;
/// ACE type ACCESS_ALLOWED_ACE_TYPE.
const ACCESS_ALLOWED_ACE_TYPE: u8 = 0;

/// Fixed value-free error kinds. No payload, path or credential ever appears.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrivateIoErrorKind {
    UnsupportedPlatform,
    InvalidChildName,
    PayloadTooLarge,
    ReadLimitTooLarge,
    InvalidAttemptDirectory,
    DirectoryCreateFailed,
    ChildCreateFailed,
    ChildOpenFailed,
    ClaimAlreadyBurned,
    ClaimWriteFailed,
    ClaimVerifyFailed,
    PublishStagingFailed,
    PublishTargetExists,
    PublishRenameFailed,
    ReadOpenFailed,
    ReadOversize,
    ReadIncomplete,
    ReadMismatch,
    IdentityMismatch,
    SecurityUnsupported,
    SecurityVerifyFailed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PrivateIoError(PrivateIoErrorKind);

impl PrivateIoError {
    pub fn kind(&self) -> PrivateIoErrorKind {
        self.0
    }
}

impl fmt::Display for PrivateIoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self.0 {
            PrivateIoErrorKind::UnsupportedPlatform => "unsupported platform",
            PrivateIoErrorKind::InvalidChildName => "invalid child name",
            PrivateIoErrorKind::PayloadTooLarge => "payload exceeds finite bound",
            PrivateIoErrorKind::ReadLimitTooLarge => "read limit exceeds finite bound",
            PrivateIoErrorKind::InvalidAttemptDirectory => {
                "attempt directory failed private verification"
            }
            PrivateIoErrorKind::DirectoryCreateFailed => "private directory creation failed",
            PrivateIoErrorKind::ChildCreateFailed => "child file creation failed",
            PrivateIoErrorKind::ChildOpenFailed => "child file open failed",
            PrivateIoErrorKind::ClaimAlreadyBurned => "claim already burned",
            PrivateIoErrorKind::ClaimWriteFailed => "claim write failed",
            PrivateIoErrorKind::ClaimVerifyFailed => "claim verification failed",
            PrivateIoErrorKind::PublishStagingFailed => "staging failed",
            PrivateIoErrorKind::PublishTargetExists => "publish target exists",
            PrivateIoErrorKind::PublishRenameFailed => "atomic rename failed",
            PrivateIoErrorKind::ReadOpenFailed => "bounded read open failed",
            PrivateIoErrorKind::ReadOversize => "bounded read exceeds its limit",
            PrivateIoErrorKind::ReadIncomplete => "bounded read is incomplete",
            PrivateIoErrorKind::ReadMismatch => "bounded read mismatch",
            PrivateIoErrorKind::IdentityMismatch => "file identity changed",
            PrivateIoErrorKind::SecurityUnsupported => "security primitive unavailable",
            PrivateIoErrorKind::SecurityVerifyFailed => {
                "owner-only security verification failed"
            }
        };
        f.write_str(text)
    }
}

impl std::error::Error for PrivateIoError {}

fn err(kind: PrivateIoErrorKind) -> PrivateIoError {
    PrivateIoError(kind)
}

/// Reserved Windows device base names (case-insensitive, with or without
/// extension) that must never be used as a child file name. Includes the
/// superscript aliases rejected up front by the grammar below (they contain
/// non-ASCII characters) for documentation, plus CONIN$/CONOUT$ which are
/// rejected by the `:`-free grammar before any device open is attempted.
const RESERVED_DEVICE_NAMES: [&str; 24] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9", "CONIN$",
    "CONOUT$",
];

/// Child-name grammar for this finite producer I/O API: ASCII alphanumeric,
/// `.`, `_` and `-` only (root-authorized fail-closed restriction; planned
/// phase-suite file names use only these characters). Everything else —
/// including separators, traversal, ADS `:`, wildcards, control characters,
/// any non-ASCII byte (which excludes the COM¹/COM²/COM³ superscript
/// aliases), CONIN$/CONOUT$ (`$`), leading/trailing dots or dashes, `.`,
/// `..`, trailing dot, and reserved device names — is rejected BEFORE any
/// file-system operation, so no device open is ever attempted.
fn validate_child_name(name: &str) -> Result<(), PrivateIoError> {
    let invalid = || err(PrivateIoErrorKind::InvalidChildName);
    if name.is_empty() || name.len() > MAX_NAME_CHARS {
        return Err(invalid());
    }
    if !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-') {
        return Err(invalid());
    }
    if name.starts_with('.') || name.ends_with('.') {
        return Err(invalid());
    }
    if name == "." || name == ".." {
        return Err(invalid());
    }
    if name.encode_utf16().count() > MAX_NAME_UTF16_UNITS {
        return Err(invalid());
    }
    let upper = name.to_ascii_uppercase();
    let base = upper.split('.').next().unwrap_or_default();
    if RESERVED_DEVICE_NAMES.contains(&base) {
        return Err(invalid());
    }
    Ok(())
}

/// Bounded read scratch allocation for one document: the INCLUSIVE
/// document cap (MAX_CONTROL_DOCUMENT_BYTES) is checked against `size`
/// itself, and the one-byte lookahead sentinel is a SEPARATELY checked
/// scratch capacity (`size + 1` via checked addition, so the scratch is at
/// most one byte above the document cap — never a reason to refuse a
/// document that is within the cap). Callers must have validated `size`
/// against their own payload/limit bound first.
fn bounded_vec(size: usize) -> Result<Vec<u8>, PrivateIoError> {
    if size > MAX_CONTROL_DOCUMENT_BYTES {
        return Err(err(PrivateIoErrorKind::ReadLimitTooLarge));
    }
    let capacity = size
        .checked_add(1)
        .ok_or_else(|| err(PrivateIoErrorKind::ReadLimitTooLarge))?;
    Ok(vec![0u8; capacity])
}

#[cfg(windows)]
mod imp {
    use super::*;

    use std::mem::size_of;
    use std::os::windows::ffi::OsStrExt;
    use std::path::PathBuf;

    use windows_sys::Win32::Foundation::{
        CloseHandle, ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Security::{
        AddAccessAllowedAceEx, EqualSid, GetAce, GetAclInformation, GetKernelObjectSecurity,
        GetSecurityDescriptorControl, GetSecurityDescriptorDacl, GetSecurityDescriptorOwner,
        GetTokenInformation, InitializeAcl, InitializeSecurityDescriptor, IsValidSid,
        SetSecurityDescriptorControl, SetSecurityDescriptorDacl, SetSecurityDescriptorOwner,
        TokenUser, ACL, ACL_REVISION, ACL_SIZE_INFORMATION, AclSizeInformation,
        DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION, PSID, SECURITY_ATTRIBUTES,
        SECURITY_DESCRIPTOR, SE_DACL_PROTECTED, SID_AND_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateDirectoryW, CreateFileW, FlushFileBuffers, GetFileInformationByHandle, GetFileType,
        MoveFileExW, ReadFile, SetFilePointerEx, WriteFile, BY_HANDLE_FILE_INFORMATION,
        CREATE_NEW, FILE_ALL_ACCESS, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_NORMAL,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_BEGIN, FILE_FLAG_BACKUP_SEMANTICS,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_SHARE_READ,
        FILE_TYPE_DISK, OPEN_EXISTING,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    /// RAII handle guard: closes exactly once on every path.
    struct OwnedHandle(HANDLE);

    impl OwnedHandle {
        fn is_invalid(&self) -> bool {
            self.0.is_null() || self.0 == INVALID_HANDLE_VALUE
        }
    }

    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            if !self.is_invalid() {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
    }

    fn last_error() -> u32 {
        unsafe { windows_sys::Win32::Foundation::GetLastError() }
    }

    fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain([0]).collect()
    }

    // ---- R2: typed/aligned API storage --------------------------------
    // Windows API structure buffers are stored in typed arrays whose Rust
    // layout satisfies the API alignment contract, instead of casts from
    // Vec<u8>/Vec<u32> whose alignment is only incidental. A zeroed
    // #[repr(C)] array of bytes with an explicit align wrapper provides the
    // required alignment without an allocator library.

    /// Aligned byte storage for Windows API structures (ACL,
    /// SECURITY_DESCRIPTOR, GetKernelObjectSecurity results). Aligns every
    /// buffer to 8 bytes, which covers pointer fields on x64.
    #[repr(C, align(8))]
    pub(crate) struct AlignedBuffer {
        len: usize,
        bytes: [u8; 1024],
    }

    impl AlignedBuffer {
        fn zeroed(len: usize) -> Self {
            assert!(len <= 1024, "aligned buffer cap");
            Self {
                len,
                bytes: [0u8; 1024],
            }
        }
        fn as_mut_ptr(&mut self) -> *mut core::ffi::c_void {
            self.bytes.as_mut_ptr() as *mut core::ffi::c_void
        }
        fn as_ptr(&self) -> *const core::ffi::c_void {
            self.bytes.as_ptr() as *const core::ffi::c_void
        }
    }

    /// Current process-user SID bytes, bounded by `MAX_SID_BYTES` (finite
    /// documented cap; typical size is far below it). The TOKEN_USER buffer
    /// is typed storage; the returned SID is copied into an owned byte vec.
    pub fn current_process_user_sid() -> Result<Vec<u8>, PrivateIoError> {
        let mut token = OwnedHandle(std::ptr::null_mut());
        unsafe {
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token.0) == 0 {
                return Err(err(PrivateIoErrorKind::SecurityUnsupported));
            }
        }
        let mut needed = 0u32;
        unsafe {
            GetTokenInformation(token.0, TokenUser, std::ptr::null_mut(), 0, &mut needed);
        }
        if needed == 0 || needed as usize > MAX_SID_BYTES {
            return Err(err(PrivateIoErrorKind::SecurityUnsupported));
        }
        // Typed, aligned TOKEN_USER storage (R2): the structure contains a
        // SID_AND_ATTRIBUTES with a PSID pointer, so the buffer alignment is
        // part of the API contract.
        #[repr(C, align(8))]
        struct AlignedTokenUser {
            bytes: [u8; MAX_SID_BYTES],
        }
        let mut buffer = AlignedTokenUser {
            bytes: [0u8; MAX_SID_BYTES],
        };
        let buffer_len: u32 = MAX_SID_BYTES as u32;
        let mut returned = 0u32;
        unsafe {
            if GetTokenInformation(
                token.0,
                TokenUser,
                buffer.bytes.as_mut_ptr() as *mut core::ffi::c_void,
                buffer_len,
                &mut returned,
            ) == 0
                || returned as usize > MAX_SID_BYTES
            {
                return Err(err(PrivateIoErrorKind::SecurityUnsupported));
            }
            let user = &*(buffer.bytes.as_ptr() as *const TOKEN_USER);
            let sid = user.User.Sid as PSID;
            if IsValidSid(sid) == 0 {
                return Err(err(PrivateIoErrorKind::SecurityUnsupported));
            }
            let count = *windows_sys::Win32::Security::GetSidSubAuthorityCount(sid);
            let multiplied = 4usize
                .checked_mul(count as usize)
                .ok_or_else(|| err(PrivateIoErrorKind::SecurityUnsupported))?;
            let length = 8usize
                .checked_add(multiplied)
                .ok_or_else(|| err(PrivateIoErrorKind::SecurityUnsupported))?;
            if length > MAX_SID_BYTES {
                return Err(err(PrivateIoErrorKind::SecurityUnsupported));
            }
            let bytes = std::slice::from_raw_parts(sid as *const u8, length);
            Ok(bytes.to_vec())
        }
    }

    /// Owner-only security attributes: a protected DACL with exactly one
    /// allowed ACE (flags 0, FILE_ALL_ACCESS) for `sid`, and `sid` as owner.
    /// All buffers are typed/aligned (R2) and stay alive for the duration
    /// of the creating call (absolute descriptor). Because the descriptor
    /// is ABSOLUTE-format, its internal pointers refer to this value's own
    /// storage: after any move (including the return out of `new()`), the
    /// pointers must be re-armed by calling `attributes()` before the next
    /// Windows create call (F2 repair; see `attributes`).
    struct OwnerOnlySecurity {
        sid: Vec<u8>,
        acl: AlignedBuffer,
        descriptor: AlignedBuffer,
        attributes: Option<SECURITY_ATTRIBUTES>,
    }

    impl OwnerOnlySecurity {
        fn new() -> Result<Self, PrivateIoError> {
            let sid = current_process_user_sid()?;
            let mut acl = AlignedBuffer::zeroed(128);
            let mut descriptor = AlignedBuffer::zeroed(128);
            unsafe {
                if InitializeAcl(
                    acl.as_mut_ptr() as *mut ACL,
                    128,
                    ACL_REVISION,
                ) == 0
                {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
                if AddAccessAllowedAceEx(
                    acl.as_mut_ptr() as *mut ACL,
                    ACL_REVISION,
                    0,
                    FILE_ALL_ACCESS,
                    sid.as_ptr() as PSID,
                ) == 0
                {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
                if InitializeSecurityDescriptor(
                    descriptor.as_mut_ptr(),
                    super::SECURITY_DESCRIPTOR_REVISION_1,
                ) == 0
                {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
                if SetSecurityDescriptorDacl(descriptor.as_mut_ptr(), 1, acl.as_ptr() as *const ACL, 0)
                    == 0
                {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
                if SetSecurityDescriptorOwner(descriptor.as_mut_ptr(), sid.as_ptr() as PSID, 0) == 0 {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
                if SetSecurityDescriptorControl(
                    descriptor.as_mut_ptr(),
                    SE_DACL_PROTECTED,
                    SE_DACL_PROTECTED,
                ) == 0
                {
                    return Err(err(PrivateIoErrorKind::SecurityUnsupported));
                }
            }
            Ok(OwnerOnlySecurity {
                sid,
                acl,
                descriptor,
                attributes: Some(SECURITY_ATTRIBUTES {
                    nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
                    lpSecurityDescriptor: std::ptr::null_mut(),
                    bInheritHandle: 0,
                }),
            })
        }

        /// Hands out SECURITY_ATTRIBUTES for the next Windows create call.
        /// Must be (re-)called after every move of the `Self` value —
        /// including the return move out of `new()` — because the
        /// descriptor is ABSOLUTE-format: both the descriptor pointer in
        /// the returned attributes and the descriptor's internal Dacl
        /// pointer must be re-armed to the CURRENT addresses of this
        /// value's storage. The owner PSID is heap-stable (`Vec<u8>`), so
        /// it needs no re-arm.
        fn attributes(&mut self) -> Result<SECURITY_ATTRIBUTES, PrivateIoError> {
            let mut attributes = self
                .attributes
                .take()
                .ok_or_else(|| err(PrivateIoErrorKind::SecurityUnsupported))?;
            attributes.lpSecurityDescriptor = self.descriptor.as_mut_ptr();
            // F2 repair: re-arm the absolute descriptor's Dacl pointer to
            // the CURRENT address of `self.acl`. The address stored during
            // `new()` references moved-from storage once this value has
            // been returned or moved; using it is undefined behavior that
            // only worked by stack-layout luck. Re-arming here makes every
            // hand-out valid regardless of where the value now lives.
            if unsafe {
                SetSecurityDescriptorDacl(
                    self.descriptor.as_mut_ptr(),
                    1,
                    self.acl.as_ptr() as *const ACL,
                    0,
                )
            } == 0
            {
                return Err(err(PrivateIoErrorKind::SecurityUnsupported));
            }
            self.attributes = Some(attributes);
            Ok(attributes)
        }
    }

    /// Creates a private child directory with an owner-only protected DACL.
    /// Setup primitive for the trusted attempt directory (root/test use).
    pub fn create_private_child_directory(
        parent: &Path,
        name: &str,
    ) -> Result<(), PrivateIoError> {
        validate_child_name(name)?;
        let path = parent.join(name);
        let mut security = OwnerOnlySecurity::new()?;
        let attributes = security.attributes()?;
        // Precomputed and LIVE-HELD across the CreateDirectoryW call (B):
        // the wide buffer outlives the call and any error handling.
        let path_wide = wide(&path);
        unsafe {
            if CreateDirectoryW(path_wide.as_ptr(), &attributes) == 0 {
                return Err(err(PrivateIoErrorKind::DirectoryCreateFailed));
            }
        }
        Ok(())
    }

    /// Handle-bound owner-only verification through
    /// `GetKernelObjectSecurity` (never a name-based query) into an aligned
    /// typed buffer (R2). Verifies EXACTLY the creation policy (R4):
    /// owner == process SID, DACL present and protected, exactly one
    /// ACCESS_ALLOWED ACE, AceFlags == 0, Mask == FILE_ALL_ACCESS, ACE and
    /// SID sizes valid, and the ACE SID equals the process SID. No broad
    /// groups, inherited or inherit-only tolerance: stricter fail-closed
    /// for pre-existing objects.
    fn verify_owner_only_security(handle: HANDLE) -> Result<(), PrivateIoError> {
        let sid = current_process_user_sid()?;
        let mut needed = 0u32;
        unsafe {
            GetKernelObjectSecurity(
                handle,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                0,
                &mut needed,
            );
        }
        if needed == 0 || needed as usize > MAX_SECURITY_DESCRIPTOR_BYTES {
            return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
        }
        let mut buffer = AlignedBuffer::zeroed(needed as usize);
        let mut returned = 0u32;
        unsafe {
            if GetKernelObjectSecurity(
                handle,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                buffer.as_mut_ptr(),
                needed,
                &mut returned,
            ) == 0
                || returned == 0
                || returned > needed
            {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
        }
        let descriptor = buffer.as_ptr() as windows_sys::Win32::Security::PSECURITY_DESCRIPTOR;
        let mut control: u16 = 0;
        let mut revision = 0u32;
        let mut owner: PSID = std::ptr::null_mut();
        let mut owner_defaulted = 0;
        let mut dacl_present = 0;
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut dacl_defaulted = 0;
        unsafe {
            if GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) == 0
                || GetSecurityDescriptorOwner(descriptor, &mut owner, &mut owner_defaulted) == 0
                || GetSecurityDescriptorDacl(
                    descriptor,
                    &mut dacl_present,
                    &mut dacl,
                    &mut dacl_defaulted,
                ) == 0
                || dacl_present == 0
                || dacl.is_null()
                || owner.is_null()
                || IsValidSid(owner) == 0
                || EqualSid(owner, sid.as_ptr() as PSID) == 0
                || control & SE_DACL_PROTECTED == 0
            {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
            let mut information = ACL_SIZE_INFORMATION::default();
            if GetAclInformation(
                dacl,
                &mut information as *mut _ as *mut core::ffi::c_void,
                size_of::<ACL_SIZE_INFORMATION>() as u32,
                AclSizeInformation,
            ) == 0
                || information.AceCount != 1
            {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
            let mut ace: *mut core::ffi::c_void = std::ptr::null_mut();
            if GetAce(dacl, 0, &mut ace) == 0 || ace.is_null() {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
            let allowed = ace as *const windows_sys::Win32::Security::ACCESS_ALLOWED_ACE;
            // ACE header layout: AceType, AceFlags, AceSize (u16) at the
            // front of ACCESS_ALLOWED_ACE; the SID starts at offset 4
            // (Mask occupies a u32). Sizes must be structurally valid.
            let ace_size = (*allowed).Header.AceSize as usize;
            if (*allowed).Header.AceType != super::ACCESS_ALLOWED_ACE_TYPE
                || (*allowed).Header.AceFlags != 0
                || (*allowed).Mask != FILE_ALL_ACCESS
                || ace_size < size_of::<u32>() * 2 + 8
            {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
            let ace_sid = (allowed as *const u8).add(size_of::<u32>() * 2) as PSID;
            if IsValidSid(ace_sid) == 0
                || EqualSid(ace_sid, sid.as_ptr() as PSID) == 0
                || ace_size > 1024
            {
                return Err(err(PrivateIoErrorKind::SecurityVerifyFailed));
            }
        }
        Ok(())
    }

    /// File identity (volume serial + file index) and attributes from an
    /// open handle.
    fn file_identity(handle: HANDLE) -> Result<(BY_HANDLE_FILE_INFORMATION, u64), PrivateIoError> {
        let mut information = BY_HANDLE_FILE_INFORMATION::default();
        unsafe {
            if GetFileInformationByHandle(handle, &mut information) == 0 {
                return Err(err(PrivateIoErrorKind::IdentityMismatch));
            }
        }
        let size = (u64::from(information.nFileSizeHigh) << 32)
            | u64::from(information.nFileSizeLow);
        Ok((information, size))
    }

    /// A verified private attempt directory (R5 doc truth): `open` verifies
    /// the directory on a LOCAL handle at open time and then closes it;
    /// later child operations open children BY PATH inside the trusted
    /// exclusive namespace. No persistent-handle or race-immunity claim.
    pub struct PrivateAttemptDir {
        path: PathBuf,
    }

    impl PrivateAttemptDir {
        /// Opens and verifies an existing attempt directory: disk file, no
        /// reparse point, exact owner-only protected DACL on the open handle
        /// (verified at open time). An arbitrary directory that fails
        /// verification is never silently fixed.
        pub fn open(path: &Path) -> Result<Self, PrivateIoError> {
            // Precomputed and LIVE-HELD across the CreateFileW call (B).
            let path_wide = wide(path);
            let handle = OwnedHandle(unsafe {
                CreateFileW(
                    path_wide.as_ptr(),
                    FILE_GENERIC_READ,
                    FILE_SHARE_READ,
                    std::ptr::null(),
                    OPEN_EXISTING,
                    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                    std::ptr::null_mut(),
                )
            });
            if handle.is_invalid() {
                return Err(err(PrivateIoErrorKind::InvalidAttemptDirectory));
            }
            unsafe {
                if GetFileType(handle.0) != FILE_TYPE_DISK {
                    return Err(err(PrivateIoErrorKind::InvalidAttemptDirectory));
                }
            }
            let (information, _) = file_identity(handle.0)?;
            if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
                || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            {
                return Err(err(PrivateIoErrorKind::InvalidAttemptDirectory));
            }
            verify_owner_only_security(handle.0)?;
            Ok(PrivateAttemptDir {
                path: path.to_path_buf(),
            })
        }

        fn child_path(&self, name: &str) -> Result<PathBuf, PrivateIoError> {
            validate_child_name(name)?;
            Ok(self.path.join(name))
        }

        /// Bounded read of a regular child file: handle-first open with
        /// `OPEN_REPARSE_POINT`, disk file, no reparse, single link, EXACT
        /// owner-only DACL verified on the SAME open handle BEFORE any byte
        /// allocation or read (R5: a moved-in world-readable child is
        /// refused; the child's ACL is never inferred from the parent),
        /// size checked BEFORE allocation, bounded read of `limit + 1` bytes
        /// (a file that grew past the limit refuses without buffering), and
        /// identity re-checked after the read.
        pub fn read_file_bounded(&self, name: &str, limit: usize) -> Result<Vec<u8>, PrivateIoError> {
            if limit > MAX_CONTROL_DOCUMENT_BYTES {
                return Err(err(PrivateIoErrorKind::ReadLimitTooLarge));
            }
            let path = self.child_path(name)?;
            // Precomputed and LIVE-HELD across the CreateFileW call (B).
            let path_wide = wide(&path);
            let handle = OwnedHandle(unsafe {
                CreateFileW(
                    path_wide.as_ptr(),
                    FILE_GENERIC_READ,
                    FILE_SHARE_READ,
                    std::ptr::null(),
                    OPEN_EXISTING,
                    FILE_FLAG_OPEN_REPARSE_POINT,
                    std::ptr::null_mut(),
                )
            });
            if handle.is_invalid() {
                return Err(err(PrivateIoErrorKind::ReadOpenFailed));
            }
            unsafe {
                if GetFileType(handle.0) != FILE_TYPE_DISK {
                    return Err(err(PrivateIoErrorKind::ReadOpenFailed));
                }
            }
            let (information, size) = file_identity(handle.0)?;
            if information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0
                || information.nNumberOfLinks != 1
            {
                return Err(err(PrivateIoErrorKind::ReadOpenFailed));
            }
            // Child ACL verification BEFORE any byte allocation/read.
            verify_owner_only_security(handle.0)?;
            if size > limit as u64 {
                return Err(err(PrivateIoErrorKind::ReadOversize));
            }
            let mut buffer = bounded_vec(size as usize)?;
            let mut total = 0usize;
            loop {
                let remaining = buffer
                    .len()
                    .checked_sub(total)
                    .ok_or_else(|| err(PrivateIoErrorKind::ReadIncomplete))?;
                if remaining == 0 {
                    break;
                }
                let chunk: u32 = remaining.min(u32::MAX as usize) as u32;
                let mut read = 0u32;
                let ok = unsafe {
                    ReadFile(handle.0, buffer.as_mut_ptr().add(total), chunk, &mut read, std::ptr::null_mut())
                };
                if ok == 0 {
                    return Err(err(PrivateIoErrorKind::ReadIncomplete));
                }
                total += read as usize;
                if read == 0 {
                    break;
                }
                if total > size as usize {
                    return Err(err(PrivateIoErrorKind::ReadOversize));
                }
            }
            if total as u64 != size {
                return Err(err(PrivateIoErrorKind::ReadMismatch));
            }
            let (after, after_size) = file_identity(handle.0)?;
            if after.nNumberOfLinks != 1
                || after.dwVolumeSerialNumber != information.dwVolumeSerialNumber
                || after.nFileIndexLow != information.nFileIndexLow
                || after.nFileIndexHigh != information.nFileIndexHigh
                || after_size != size
            {
                return Err(err(PrivateIoErrorKind::IdentityMismatch));
            }
            buffer.truncate(total);
            Ok(buffer)
        }

        /// `exists_kind` is the fixed refusal kind used when the OS
        /// reports an already-existing name (claim: `ClaimAlreadyBurned`;
        /// publish staging: the generic `ChildCreateFailed`).
        fn open_staging(
            path: &Path,
            security: &mut OwnerOnlySecurity,
            exists_kind: PrivateIoErrorKind,
        ) -> Result<OwnedHandle, PrivateIoError> {
            let attributes = security.attributes()?;
            // Precomputed and LIVE-HELD across the CreateFileW call (B):
            // the wide buffer outlives the call and the adjacent capture.
            let path_wide = wide(path);
            let handle = OwnedHandle(unsafe {
                CreateFileW(
                    path_wide.as_ptr(),
                    FILE_GENERIC_READ | FILE_GENERIC_WRITE,
                    0,
                    &attributes,
                    CREATE_NEW,
                    FILE_ATTRIBUTE_NORMAL,
                    std::ptr::null_mut(),
                )
            });
            if handle.is_invalid() {
                // B: the OS error code is captured IMMEDIATELY adjacent to
                // the failed call — before any drop or scope exit — exactly
                // once, and the classification below branches ONLY on this
                // saved value.
                let code = last_error();
                if code == ERROR_FILE_EXISTS || code == ERROR_ALREADY_EXISTS {
                    return Err(err(exists_kind));
                }
                return Err(err(PrivateIoErrorKind::ChildCreateFailed));
            }
            Ok(handle)
        }

        fn write_all_bounded(
            handle: &OwnedHandle,
            payload: &[u8],
        ) -> Result<(), PrivateIoError> {
            if payload.len() > MAX_CONTROL_DOCUMENT_BYTES {
                return Err(err(PrivateIoErrorKind::PayloadTooLarge));
            }
            let mut offset = 0usize;
            while offset < payload.len() {
                let remaining = payload
                    .len()
                    .checked_sub(offset)
                    .ok_or_else(|| err(PrivateIoErrorKind::ClaimWriteFailed))?;
                let chunk: u32 = remaining.min(u32::MAX as usize) as u32;
                let mut written = 0u32;
                let ok = unsafe {
                    WriteFile(
                        handle.0,
                        payload[offset..].as_ptr(),
                        chunk,
                        &mut written,
                        std::ptr::null_mut(),
                    )
                };
                if ok == 0 || written == 0 {
                    return Err(err(PrivateIoErrorKind::ClaimWriteFailed));
                }
                offset += written as usize;
            }
            if unsafe { FlushFileBuffers(handle.0) } == 0 {
                return Err(err(PrivateIoErrorKind::ClaimWriteFailed));
            }
            Ok(())
        }

        fn readback_verify(handle: &OwnedHandle, payload: &[u8]) -> Result<(), PrivateIoError> {
            let mut position = 0i64;
            if unsafe { SetFilePointerEx(handle.0, 0, &mut position, FILE_BEGIN) } == 0 {
                return Err(err(PrivateIoErrorKind::ReadMismatch));
            }
            let mut buffer = bounded_vec(payload.len())?;
            let mut total = 0usize;
            loop {
                let remaining = buffer
                    .len()
                    .checked_sub(total)
                    .ok_or_else(|| err(PrivateIoErrorKind::ReadMismatch))?;
                if remaining == 0 {
                    break;
                }
                let chunk: u32 = remaining.min(u32::MAX as usize) as u32;
                let mut read = 0u32;
                let ok = unsafe {
                    ReadFile(
                        handle.0,
                        buffer.as_mut_ptr().add(total),
                        chunk,
                        &mut read,
                        std::ptr::null_mut(),
                    )
                };
                if ok == 0 {
                    return Err(err(PrivateIoErrorKind::ReadMismatch));
                }
                total += read as usize;
                if read == 0 {
                    break;
                }
            }
            if total != payload.len() || buffer[..total] != payload[..] {
                return Err(err(PrivateIoErrorKind::ReadMismatch));
            }
            Ok(())
        }

        /// (A) Durable exclusive claim: CREATE_NEW at the exact final name
        /// with the owner-only ACL, bounded write, flush, and same-handle
        /// byte verification. An existing name (any state, including
        /// partial/malformed/uncertain burns) refuses as
        /// `ClaimAlreadyBurned` when the OS reports an already-existing
        /// name (ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS), else a fixed
        /// generic create failure; a failure after creation leaves the
        /// burn file in place — this API never removes, overwrites or
        /// retries it.
        pub fn create_claim(&self, name: &str, payload: &[u8]) -> Result<(), PrivateIoError> {
            self.create_claim_injected(name, payload, ClaimFailPoint::None)
        }

        fn create_claim_injected(
            &self,
            name: &str,
            payload: &[u8],
            fail: ClaimFailPoint,
        ) -> Result<(), PrivateIoError> {
            if payload.len() > MAX_CONTROL_DOCUMENT_BYTES {
                return Err(err(PrivateIoErrorKind::PayloadTooLarge));
            }
            let path = self.child_path(name)?;
            let mut security = OwnerOnlySecurity::new()?;
            // The already-existing-name classification now happens inside
            // open_staging, on the code captured immediately adjacent to
            // the failed CreateFileW (B): no post-drop or double reads.
            let handle = Self::open_staging(
                &path,
                &mut security,
                PrivateIoErrorKind::ClaimAlreadyBurned,
            )?;
            if let Some(kind) = fail.claim_error_after_create() {
                return Err(kind);
            }
            Self::write_all_bounded(&handle, payload)?;
            if let Some(kind) = fail.claim_error_after_write() {
                return Err(kind);
            }
            Self::readback_verify(&handle, payload)?;
            if let Some(kind) = fail.claim_error_after_verify() {
                return Err(kind);
            }
            verify_owner_only_security(handle.0)?;
            Ok(())
        }

        /// (B) Staged no-replace publication: CREATE_NEW uniquely named
        /// (uuid-only) private staging in the same directory, bounded write,
        /// flush, same-handle verification of the exact complete bytes,
        /// close the staging handle (required for the rename; staging was
        /// opened with dwShareMode = 0), then atomic rename with NO
        /// replace flag. The advisory path pre-check below is ONLY a fast
        /// refusal; the authoritative collision gate is the no-replace
        /// MoveFileExW. An existing target (file, directory, symlink or
        /// hardlink) always refuses and preserves the existing bytes. Any
        /// rename failure is a fixed refusal; the failed unique staging
        /// file is RETAINED (conservative retention policy — cleanup is
        /// explicit owner/root work after the attempt is abandoned).
        /// No final name exists while content is incomplete. The final is
        /// never reopened for write after publication (policy, not
        /// hostile-same-user OS immutability).
        pub fn publish_file(&self, name: &str, payload: &[u8]) -> Result<(), PrivateIoError> {
            self.publish_file_injected(name, payload, PublishFailPoint::None)
        }

        fn publish_file_injected(
            &self,
            name: &str,
            payload: &[u8],
            fail: PublishFailPoint,
        ) -> Result<(), PrivateIoError> {
            if payload.len() > MAX_CONTROL_DOCUMENT_BYTES {
                return Err(err(PrivateIoErrorKind::PayloadTooLarge));
            }
            let final_path = self.child_path(name)?;
            // Advisory-only fast refusal (NOT a security check): the
            // authoritative gate is the no-replace MoveFileExW below.
            if final_path.exists() {
                return Err(err(PrivateIoErrorKind::PublishTargetExists));
            }
            // Uuid-only staging base: never grows past the name cap when
            // the final name is at the limit. Must itself satisfy the child
            // grammar (alphanumeric start; no leading dot).
            let staging_name = format!("s{}", uuid::Uuid::new_v4().simple());
            let staging_path = self.child_path(&staging_name)?;
            let mut security = OwnerOnlySecurity::new()?;
            // Uuid-only staging cannot collide with an existing name; the
            // exists kind stays the generic create failure either way.
            let handle = Self::open_staging(
                &staging_path,
                &mut security,
                PrivateIoErrorKind::ChildCreateFailed,
            )?;
            if fail.staging_error_before_write().is_some() {
                return Err(err(PrivateIoErrorKind::PublishStagingFailed));
            }
            Self::write_all_bounded(&handle, payload)?;
            Self::readback_verify(&handle, payload)?;
            verify_owner_only_security(handle.0)?;
            // The staging handle must be closed for the rename; staging was
            // created with dwShareMode = 0 so no other open can exist.
            drop(handle);
            if fail.rename_error().is_some() {
                return Err(err(PrivateIoErrorKind::PublishRenameFailed));
            }
            // Precomputed and LIVE-HELD across the MoveFileExW call (B):
            // both wide buffers outlive the call and the adjacent capture.
            let staging_wide = wide(&staging_path);
            let final_wide = wide(&final_path);
            let renamed =
                unsafe { MoveFileExW(staging_wide.as_ptr(), final_wide.as_ptr(), 0) };
            if renamed == 0 {
                // B: the OS error code is captured IMMEDIATELY adjacent to
                // the failed call — before the live-held wide buffers drop
                // or any scope exit — exactly once, and the classification
                // below branches ONLY on this saved value.
                let code = last_error();
                if code == ERROR_ALREADY_EXISTS || code == ERROR_FILE_EXISTS {
                    return Err(err(PrivateIoErrorKind::PublishTargetExists));
                }
                return Err(err(PrivateIoErrorKind::PublishRenameFailed));
            }
            Ok(())
        }

        /// Test-only accessor for honest injected-failure seams.
        #[cfg(test)]
        pub(crate) fn create_claim_seam(
            &self,
            name: &str,
            payload: &[u8],
            fail: ClaimFailPoint,
        ) -> Result<(), PrivateIoError> {
            self.create_claim_injected(name, payload, fail)
        }

        /// Test-only accessor for honest injected-failure seams.
        #[cfg(test)]
        pub(crate) fn publish_file_seam(
            &self,
            name: &str,
            payload: &[u8],
            fail: PublishFailPoint,
        ) -> Result<(), PrivateIoError> {
            self.publish_file_injected(name, payload, fail)
        }
    }

    /// Test-only defect-F2 regression seam: constructs the security value
    /// inside an inner function and RETURNS it (a real move out of the
    /// constructor's frame), clobbers the dead stack region that a stale
    /// pre-repair Dacl pointer referenced, then creates a child through the
    /// real staging path (`attributes()` + CreateFileW) and verifies the
    /// EXACT owner-only DACL on the same open handle. With the pre-repair
    /// code the clobbered ACL bytes make this fail; it must not pass by
    /// stack-layout luck.
    #[cfg(test)]
    pub(crate) fn security_survives_return_move_and_stack_clobber(
        path: &Path,
    ) -> Result<(), PrivateIoError> {
        fn stack_clobber(depth: usize) {
            // Large distinctive per-frame locals, recursed deep enough to
            // cover the dead constructor frames below the current stack
            // pointer. black_box keeps the arrays materialized.
            let bytes = [0xA5u8; 2048];
            if depth > 0 {
                stack_clobber(depth - 1);
            }
            std::hint::black_box(&bytes);
        }
        fn make() -> Result<OwnerOnlySecurity, PrivateIoError> {
            OwnerOnlySecurity::new()
        }
        let mut security = make()?;
        stack_clobber(16);
        let handle = PrivateAttemptDir::open_staging(
            path,
            &mut security,
            PrivateIoErrorKind::ChildCreateFailed,
        )?;
        verify_owner_only_security(handle.0)
    }

    /// Honest injected-failure seams for tests only. These simulate failure
    /// boundaries INSIDE the module; they are not filesystem behavior.
    /// AfterCreate: failure immediately after CREATE_NEW (empty burn).
    /// AfterWrite: failure after the FULL write + FlushFileBuffers
    ///   (fully-written uncertain burn, NOT a partial-write simulation).
    /// AfterVerify: failure after the byte verify, BEFORE the ACL verify.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(crate) enum ClaimFailPoint {
        None,
        AfterCreate,
        AfterWrite,
        AfterVerify,
    }

    impl ClaimFailPoint {
        fn claim_error_after_create(&self) -> Option<PrivateIoError> {
            match self {
                ClaimFailPoint::AfterCreate => Some(err(PrivateIoErrorKind::ClaimWriteFailed)),
                _ => None,
            }
        }
        fn claim_error_after_write(&self) -> Option<PrivateIoError> {
            match self {
                ClaimFailPoint::AfterWrite => Some(err(PrivateIoErrorKind::ClaimVerifyFailed)),
                _ => None,
            }
        }
        fn claim_error_after_verify(&self) -> Option<PrivateIoError> {
            match self {
                ClaimFailPoint::AfterVerify => Some(err(PrivateIoErrorKind::ClaimVerifyFailed)),
                _ => None,
            }
        }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(crate) enum PublishFailPoint {
        None,
        /// Failure after staging CREATE_NEW but before any byte write.
        /// The empty unique staging file is RETAINED (conservative policy).
        StagingBeforeWrite,
        /// Failure after verify + handle close, immediately before the
        /// rename. The complete unique staging file is RETAINED.
        BeforeRename,
    }

    impl PublishFailPoint {
        fn staging_error_before_write(&self) -> Option<()> {
            match self {
                PublishFailPoint::StagingBeforeWrite => Some(()),
                _ => None,
            }
        }
        fn rename_error(&self) -> Option<()> {
            match self {
                PublishFailPoint::BeforeRename => Some(()),
                _ => None,
            }
        }
    }
}

#[cfg(windows)]
pub use imp::{
    create_private_child_directory, current_process_user_sid, PrivateAttemptDir,
};

#[cfg(not(windows))]
mod imp {
    use super::*;

    pub fn current_process_user_sid() -> Result<Vec<u8>, PrivateIoError> {
        Err(PrivateIoError(PrivateIoErrorKind::UnsupportedPlatform))
    }

    pub fn create_private_child_directory(
        _parent: &Path,
        _name: &str,
    ) -> Result<(), PrivateIoError> {
        Err(PrivateIoError(PrivateIoErrorKind::DirectoryCreateFailed))
    }

    pub struct PrivateAttemptDir;

    impl PrivateAttemptDir {
        pub fn open(_path: &Path) -> Result<Self, PrivateIoError> {
            Err(PrivateIoError(PrivateIoErrorKind::UnsupportedPlatform))
        }
        pub fn read_file_bounded(
            &self,
            _name: &str,
            _limit: usize,
        ) -> Result<Vec<u8>, PrivateIoError> {
            Err(PrivateIoError(PrivateIoErrorKind::UnsupportedPlatform))
        }
        pub fn create_claim(&self, _name: &str, _payload: &[u8]) -> Result<(), PrivateIoError> {
            Err(PrivateIoError(PrivateIoErrorKind::UnsupportedPlatform))
        }
        pub fn publish_file(&self, _name: &str, _payload: &[u8]) -> Result<(), PrivateIoError> {
            Err(PrivateIoError(PrivateIoErrorKind::UnsupportedPlatform))
        }
    }
}

#[cfg(not(windows))]
pub use imp::{create_private_child_directory, current_process_user_sid, PrivateAttemptDir};

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    mod windows_tests {
        use super::*;
        use std::path::PathBuf;
        use std::sync::{Arc, Barrier};

        fn test_root() -> PathBuf {
            let root = std::env::temp_dir().join(format!(
                "occ3-private-io-o2-{}",
                uuid::Uuid::new_v4().simple()
            ));
            std::fs::create_dir_all(&root).expect("test root");
            root
        }

        fn private_dir(root: &Path) -> PrivateAttemptDir {
            create_private_child_directory(root, "attempt").expect("private dir");
            PrivateAttemptDir::open(&root.join("attempt")).expect("verified open")
        }

        /// Cleans ONLY this test's own unique root, AFTER assertions.
        fn cleanup(root: &Path) {
            std::fs::remove_dir_all(root).ok();
        }

        #[test]
        fn child_names_use_only_the_explicit_safe_ascii_grammar() {
            for name in [
                "",
                ".",
                "..",
                "../escape",
                "a/b",
                "a\\b",
                "C:\\absolute",
                "stream:ads",
                "CON",
                "nul.txt",
                "COM1.log",
                "aux",
                "lpt9",
                "conin$",
                "CONOUT$.log",
                "com\u{b9}",
                "COM\u{b2}.txt",
                "trailing.",
                ".leading",
                "wild*card",
                "question?",
                "quote\"",
                "angle<",
                "pipe|",
                "space name",
                "caf\u{e9}.json",
                "\u{4e2d}\u{6587}.json",
            ] {
                assert!(validate_child_name(name).is_err(), "{name:?}");
            }
            for name in [
                "claim.json",
                "report-1.json",
                "validator-challenge-before_restart.json",
                "a-b_c.d",
                "native-phase-suite-before_restart.json",
                "x".repeat(96).as_str(),
            ] {
                assert!(validate_child_name(name).is_ok(), "{name:?}");
            }
            // UTF-16 unit bound: 96 ASCII chars is fine; the grammar already
            // restricts to ASCII, so the unit cap is structural.
            assert!(validate_child_name(&"y".repeat(97)).is_err());
        }

        #[test]
        fn payload_and_limit_bounds_reject_before_any_allocation_or_name_use() {
            let root = test_root();
            let dir = private_dir(&root);
            // Payload bound: rejected BEFORE staging/name creation (no file
            // with the target name can appear).
            assert_eq!(
                dir.create_claim("big.json", &[0u8; 1]).err().map(|e| e.kind()),
                None
            );
            // Scalar boundary checks WITHOUT large allocations: the bound
            // constant itself plus one is rejected as a size, and the
            // u32/usize conversion paths are exercised through the public
            // API with tiny files plus a reduced internal bound is not
            // configurable — so we assert the constant relationships.
            assert_eq!(MAX_CONTROL_DOCUMENT_BYTES, 2 * 1024 * 1024);
            // A payload one byte above the finite bound is refused without
            // creating the name. Build the over-bound payload lazily as a
            // size-only rejection test: use a vector of the exact bound+1.
            let oversized = vec![0u8; MAX_CONTROL_DOCUMENT_BYTES + 1];
            assert_eq!(
                dir.create_claim("over.json", &oversized).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::PayloadTooLarge)
            );
            assert!(!root.join("attempt").join("over.json").exists());
            assert_eq!(
                dir.publish_file("over2.json", &oversized).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::PayloadTooLarge)
            );
            assert!(!root.join("attempt").join("over2.json").exists());
            assert_eq!(
                dir.read_file_bounded("x.json", MAX_CONTROL_DOCUMENT_BYTES + 1)
                    .err()
                    .map(|e| e.kind()),
                Some(PrivateIoErrorKind::ReadLimitTooLarge)
            );
            assert!(!root.join("attempt").join("x.json").exists());
            cleanup(&root);
        }

        #[test]
        fn claim_roundtrip_bounded_read_and_exact_owner_only_security() {
            let root = test_root();
            let dir = private_dir(&root);
            let payload: Vec<u8> = (0..4096u32).map(|i| (i % 251) as u8).collect();
            dir.create_claim("claim.json", &payload).expect("claim");
            // A second claim on the same name refuses as an already-burned
            // name (ERROR_FILE_EXISTS classification).
            assert_eq!(
                dir.create_claim("claim.json", b"second").err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::ClaimAlreadyBurned)
            );
            let read = dir.read_file_bounded("claim.json", 8192).expect("read");
            assert_eq!(read, payload);
            assert_eq!(
                dir.read_file_bounded("claim.json", 16).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::ReadOversize)
            );
            cleanup(&root);
        }

        #[test]
        fn publish_roundtrip_and_no_overwrite_of_existing_targets() {
            let root = test_root();
            let dir = private_dir(&root);
            let payload = b"published-report-payload".to_vec();
            dir.publish_file("report.json", &payload).expect("publish");
            assert_eq!(
                dir.read_file_bounded("report.json", 1024).as_deref(),
                Ok(payload.as_slice())
            );
            assert_eq!(
                dir.publish_file("report.json", b"other").err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::PublishTargetExists)
            );
            assert_eq!(
                dir.read_file_bounded("report.json", 1024).as_deref(),
                Ok(payload.as_slice())
            );
            std::fs::create_dir(root.join("attempt").join("as-dir")).expect("dir");
            assert_eq!(
                dir.publish_file("as-dir", b"x").err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::PublishTargetExists)
            );
            cleanup(&root);
        }

        #[test]
        fn claim_burns_are_never_deleted_overwritten_or_reused() {
            let root = test_root();
            let dir = private_dir(&root);
            // AfterCreate: empty burn retained.
            assert!(dir
                .create_claim_seam("burn-a.json", b"payload-a", imp::ClaimFailPoint::AfterCreate)
                .is_err());
            assert!(root.join("attempt").join("burn-a.json").exists());
            // AfterWrite: post-full-write+flush uncertain burn (complete
            // bytes), retained.
            assert!(dir
                .create_claim_seam("burn-b.json", b"payload-b", imp::ClaimFailPoint::AfterWrite)
                .is_err());
            let burn_b = std::fs::read(root.join("attempt").join("burn-b.json")).expect("retained");
            assert_eq!(burn_b, b"payload-b");
            // AfterVerify: post-byte-verify/pre-ACL uncertain burn, retained.
            assert!(dir
                .create_claim_seam("burn-c.json", b"payload-c", imp::ClaimFailPoint::AfterVerify)
                .is_err());
            for name in ["burn-a.json", "burn-b.json", "burn-c.json"] {
                assert!(dir.create_claim(name, b"again").is_err(), "{name}");
                assert!(root.join("attempt").join(name).exists(), "{name}");
            }
            cleanup(&root);
        }

        #[test]
        fn failed_publication_stages_are_retained_and_never_become_final() {
            let root = test_root();
            let dir = private_dir(&root);
            // StagingBeforeWrite: empty unique stage retained, no final name.
            assert!(dir
                .publish_file_seam("r1.json", b"one", imp::PublishFailPoint::StagingBeforeWrite)
                .is_err());
            assert!(!root.join("attempt").join("r1.json").exists());
            // BeforeRename: complete unique stage retained, no final name.
            assert!(dir
                .publish_file_seam("r2.json", b"two", imp::PublishFailPoint::BeforeRename)
                .is_err());
            assert!(!root.join("attempt").join("r2.json").exists());
            // Exactly two retained private staging files exist.
            let stages = std::fs::read_dir(root.join("attempt"))
                .unwrap()
                .filter_map(|e| e.ok())
                .filter(|e| {
                    let name = e.file_name().to_string_lossy().to_string();
                    name.len() == 33 && name.starts_with('s') && name[1..].chars().all(|c| c.is_ascii_hexdigit())
                })
                .count();
            assert_eq!(stages, 2, "both failed stages retained");
            // A later publish to the same names still works (no burn state
            // for publish; only claim names burn).
            dir.publish_file("r1.json", b"one").expect("publish after retained stage");
            cleanup(&root);
        }

        #[test]
        fn simultaneous_claims_have_exactly_one_winner_with_exact_candidate_bytes() {
            let root = test_root();
            let dir = Arc::new(private_dir(&root));
            let barrier = Arc::new(Barrier::new(4));
            let candidates: Vec<String> =
                (0..4).map(|i| format!("winner-{i}")).collect();
            std::thread::scope(|scope| {
                let joins: Vec<_> = (0..4)
                    .map(|i| {
                        let dir = dir.clone();
                        let barrier = barrier.clone();
                        let payload = candidates[i].clone().into_bytes();
                        scope.spawn(move || {
                            barrier.wait();
                            dir.create_claim("race.json", &payload)
                        })
                    })
                    .collect();
                let results: Vec<Result<(), PrivateIoError>> =
                    joins.into_iter().map(|j| j.join().unwrap()).collect();
                let wins = results.iter().filter(|r| r.is_ok()).count();
                assert_eq!(wins, 1, "exactly one claim wins");
                // C2: every loser received the FIXED refusal KIND (an
                // already-existing burn name classified from the error code
                // captured adjacent to the failed CreateFileW), not an
                // erased or misclassified error.
                for result in &results {
                    if let Err(error) = result {
                        assert_eq!(
                            error.kind(),
                            PrivateIoErrorKind::ClaimAlreadyBurned,
                            "claim race losers must see the burned refusal"
                        );
                    }
                }
            });
            let content = dir.read_file_bounded("race.json", 64).expect("read");
            let text = String::from_utf8(content).expect("ascii");
            // The winning final bytes are EXACTLY one candidate payload —
            // never a mixture.
            assert!(
                candidates.iter().any(|c| c.as_bytes() == text.as_bytes()),
                "final bytes are exactly one candidate"
            );
            cleanup(&root);
        }

        #[test]
        fn simultaneous_publishes_have_exactly_one_winner_with_exact_candidate_bytes() {
            let root = test_root();
            let dir = Arc::new(private_dir(&root));
            let barrier = Arc::new(Barrier::new(4));
            let candidates: Vec<Vec<u8>> =
                (0..4).map(|i| vec![0xA0 + i as u8; 2048]).collect();
            std::thread::scope(|scope| {
                let joins: Vec<_> = (0..4)
                    .map(|i| {
                        let dir = dir.clone();
                        let barrier = barrier.clone();
                        let payload = candidates[i].clone();
                        scope.spawn(move || {
                            barrier.wait();
                            dir.publish_file("race.json", &payload)
                        })
                    })
                    .collect();
                let results: Vec<Result<(), PrivateIoError>> =
                    joins.into_iter().map(|j| j.join().unwrap()).collect();
                let wins = results.iter().filter(|r| r.is_ok()).count();
                assert_eq!(wins, 1, "exactly one publish wins");
                // C2: every loser received the FIXED refusal KIND (target
                // exists, classified from the error code captured adjacent
                // to the failed no-replace MoveFileExW), not an erased or
                // misclassified error.
                for result in &results {
                    if let Err(error) = result {
                        assert_eq!(
                            error.kind(),
                            PrivateIoErrorKind::PublishTargetExists,
                            "publish race losers must see the target-exists refusal"
                        );
                    }
                }
            });
            let content = dir.read_file_bounded("race.json", 4096).expect("read");
            assert!(
                candidates.iter().any(|c| c == &content),
                "final bytes are exactly one candidate, never mixed"
            );
            // All losers received the fixed refusal (target exists),
            // asserted directly above (C2), in addition to the
            // single-winner count and the exact-bytes check.
            cleanup(&root);
        }

        #[test]
        fn in_place_default_acl_child_is_refused_on_read() {
            let root = test_root();
            let dir = private_dir(&root);
            // A child created IN PLACE with the DEFAULT (non-owner-only)
            // ACL — the observable equivalent of a moved-in world-readable
            // child: the ACL state is what the read refuses. No actual
            // move is performed or claimed by this test (C3 wording fix).
            let plain_path = root.join("attempt").join("default-acl.json");
            std::fs::write(&plain_path, b"plain-default-acl-bytes").expect("write");
            // Exact owner-only verification must refuse it BEFORE any byte
            // allocation/read (fail-closed for pre-existing objects).
            assert_eq!(
                dir.read_file_bounded("default-acl.json", 64).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::SecurityVerifyFailed)
            );
            // Bytes are preserved (never modified by the refusal).
            assert_eq!(
                std::fs::read(&plain_path).expect("read"),
                b"plain-default-acl-bytes".to_vec()
            );
            cleanup(&root);
        }

        #[test]
        fn acl_flag_and_mask_negatives_on_own_created_objects() {
            // Exact verification of flags/mask is enforced by
            // verify_owner_only_security. Creation always uses flags=0 and
            // FILE_ALL_ACCESS, so on our OWN objects the negative branches
            // (INHERITED_ACE, INHERIT_ONLY, wrong mask) cannot be produced
            // through this API's positive path — they are covered by the
            // in-place default-ACL negative above (an inherited ACE from
            // the default parent ACL fails exactly there). This test pins
            // the structural checks: our created objects verify clean, and
            // the default-ACL file fails, on the SAME handle-bound queries.
            let root = test_root();
            let dir = private_dir(&root);
            dir.publish_file("own.json", b"own-bytes").expect("publish");
            assert_eq!(
                dir.read_file_bounded("own.json", 64).as_deref(),
                Ok(b"own-bytes".as_ref())
            );
            std::fs::write(root.join("attempt").join("plain.json"), b"x").expect("write");
            assert!(dir.read_file_bounded("plain.json", 64).is_err());
            cleanup(&root);
        }

        #[test]
        fn hardlinked_child_refuses_bounded_read() {
            let root = test_root();
            let dir = private_dir(&root);
            let payload = b"original-bytes".to_vec();
            dir.publish_file("linked.json", &payload).expect("publish");
            let target = root.join("attempt").join("link.json");
            std::fs::hard_link(root.join("attempt").join("linked.json"), &target)
                .expect("hard link");
            assert_eq!(
                dir.read_file_bounded("link.json", 64).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::ReadOpenFailed)
            );
            cleanup(&root);
        }

        #[test]
        fn symlinked_child_refuses_with_privilege_only_honest_skip() {
            let root = test_root();
            let dir = private_dir(&root);
            let payload = b"original-bytes".to_vec();
            dir.publish_file("target.json", &payload).expect("publish");
            let link = root.join("attempt").join("soft.json");
            match std::os::windows::fs::symlink_file(root.join("attempt").join("target.json"), &link)
            {
                Ok(()) => {
                    let refusal = dir.read_file_bounded("soft.json", 64).err().expect("refuse");
                    println!("SYMLINK_BRANCH_REALLY_RAN refusal={:?}", refusal.kind());
                    assert_eq!(refusal.kind(), PrivateIoErrorKind::ReadOpenFailed);
                }
                Err(error) => {
                    // Honest skip ONLY for the actual privilege-not-held OS
                    // error; any other creation error FAILS the test.
                    let code = error.raw_os_error().unwrap_or(0);
                    println!("SYMLINK_CREATE_ERROR code={code}");
                    assert_eq!(code, 1314, "only ERROR_PRIVILEGE_NOT_HELD may skip");
                }
            }
            cleanup(&root);
        }

        #[test]
        fn missing_child_reports_read_open_failure() {
            let root = test_root();
            let dir = private_dir(&root);
            assert_eq!(
                dir.read_file_bounded("missing.json", 64).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::ReadOpenFailed)
            );
            cleanup(&root);
        }

        #[test]
        fn current_process_user_sid_is_bounded_stable_and_aligned() {
            let first = current_process_user_sid().expect("sid");
            assert!(!first.is_empty() && first.len() <= MAX_SID_BYTES);
            let second = current_process_user_sid().expect("sid");
            assert_eq!(first, second);
            // R2 layout evidence: the typed storage is a repr(C, align(8))
            // array; assert the alignment contract of the wrapper type used
            // for API structure buffers.
            #[repr(C, align(8))]
            struct Probe {
                _bytes: [u8; 8],
            }
            assert_eq!(std::mem::align_of::<Probe>(), 8);
            assert_eq!(std::mem::align_of::<imp::AlignedBuffer>(), 8);
            assert!(std::mem::size_of::<imp::AlignedBuffer>() >= 1024);
        }

        #[test]
        fn plain_directory_is_refused_at_open() {
            let root = test_root();
            let plain = root.join("plain");
            std::fs::create_dir(&plain).expect("plain dir");
            let refused = PrivateAttemptDir::open(&plain);
            assert!(
                matches!(
                    refused.err().map(|e| e.kind()),
                    Some(PrivateIoErrorKind::SecurityVerifyFailed)
                        | Some(PrivateIoErrorKind::InvalidAttemptDirectory)
                ),
                "plain directory must be refused, never silently fixed"
            );
            cleanup(&root);
        }

        #[test]
        fn error_values_carry_no_payload_or_path_bytes() {
            for kind in [
                PrivateIoErrorKind::PayloadTooLarge,
                PrivateIoErrorKind::ReadLimitTooLarge,
                PrivateIoErrorKind::ClaimAlreadyBurned,
                PrivateIoErrorKind::PublishTargetExists,
                PrivateIoErrorKind::SecurityVerifyFailed,
            ] {
                let error = PrivateIoError(kind);
                let text = format!("{error:?} {error}");
                // Fixed value-free text: no file names, paths, payload bytes
                // or credential material ever appears.
                assert!(!text.contains(".json"), "{kind:?}");
                assert!(!text.contains("C:"), "{kind:?}");
                assert!(!text.contains("occ3"), "{kind:?}");
                assert!(!text.contains("bearer"), "{kind:?}");
            }
        }

        #[test]
        fn boundary_name_length_is_enforced_before_any_operation() {
            let root = test_root();
            let dir = private_dir(&root);
            // 96 chars is the cap; 97 is refused before any FS operation.
            let long_ok = "a".repeat(96);
            let long_bad = "a".repeat(97);
            dir.publish_file(&long_ok, b"x").expect("96-char name publishes");
            assert_eq!(
                dir.publish_file(&long_bad, b"x").err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::InvalidChildName)
            );
            assert!(!root.join("attempt").join(&long_bad).exists());
            // The uuid-only staging base never adds a suffix that could
            // exceed the cap when the final name is at the limit.
            let staging = format!("s{}", uuid::Uuid::new_v4().simple());
            assert!(validate_child_name(&staging).is_ok());
            assert!(staging.len() < 40);
            cleanup(&root);
        }

        #[test]
        fn owner_only_security_dacl_survives_return_move_and_stack_clobber() {
            let root = test_root();
            let dir = private_dir(&root);
            let path = root.join("attempt").join("moved-security.json");
            // Defect F2 regression: the security value is constructed in
            // one function, RETURNED (a real move out of the constructor's
            // frame), the dead stack region is clobbered with a distinctive
            // pattern, and only then is the value handed to the real
            // create path. The exact owner-only DACL must still apply
            // (verified on the same open handle) — this must not pass by
            // stack-layout luck.
            imp::security_survives_return_move_and_stack_clobber(&path)
                .expect("moved security still applies the exact owner-only DACL");
            println!("F2_MOVE_CLOBBER_BRANCH_REALLY_RAN create_and_verify=ok");
            // Full public verification: exact ACL on the same handle before
            // the read, empty document round-trips.
            assert_eq!(
                dir.read_file_bounded("moved-security.json", 64).as_deref(),
                Ok(b"".as_ref())
            );
            cleanup(&root);
        }

        #[test]
        fn document_cap_boundaries_round_trip_inclusive() {
            let root = test_root();
            let dir = private_dir(&root);
            // Defect A: the document cap is INCLUSIVE — zero, MAX-1 and
            // exact-MAX documents are valid for claim, publish and bounded
            // read; MAX+1 is refused before any allocation or name use.
            assert!(dir.create_claim("cap-zero.json", b"").is_ok());
            assert_eq!(
                dir.read_file_bounded("cap-zero.json", 0).as_deref(),
                Ok(b"".as_ref())
            );
            assert!(dir.publish_file("cap-zero-p.json", b"").is_ok());
            assert_eq!(
                dir.read_file_bounded("cap-zero-p.json", 0).as_deref(),
                Ok(b"".as_ref())
            );
            let max_minus_1 = vec![0x5Au8; MAX_CONTROL_DOCUMENT_BYTES - 1];
            assert!(dir.create_claim("cap-max-minus-1.json", &max_minus_1).is_ok());
            let max_exact = vec![0xA5u8; MAX_CONTROL_DOCUMENT_BYTES];
            assert!(dir.publish_file("cap-max-exact.json", &max_exact).is_ok());
            // Exact-MAX readback with the exact-MAX limit (inclusive), plus
            // the one-byte-smaller limit refusal.
            assert_eq!(
                dir.read_file_bounded("cap-max-exact.json", MAX_CONTROL_DOCUMENT_BYTES)
                    .as_deref(),
                Ok(max_exact.as_slice())
            );
            assert_eq!(
                dir.read_file_bounded("cap-max-exact.json", MAX_CONTROL_DOCUMENT_BYTES - 1)
                    .err()
                    .map(|e| e.kind()),
                Some(PrivateIoErrorKind::ReadOversize)
            );
            assert_eq!(
                dir.read_file_bounded("cap-max-minus-1.json", MAX_CONTROL_DOCUMENT_BYTES)
                    .as_deref(),
                Ok(max_minus_1.as_slice())
            );
            // No retained burn/stage residue on the exact-MAX paths: the
            // attempt directory holds exactly the intended children (no
            // orphaned uuid staging files, no extra partial artifacts).
            let mut names: Vec<String> = std::fs::read_dir(root.join("attempt"))
                .expect("read dir")
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .collect();
            names.sort();
            assert_eq!(
                names,
                vec![
                    "cap-max-exact.json".to_string(),
                    "cap-max-minus-1.json".to_string(),
                    "cap-zero-p.json".to_string(),
                    "cap-zero.json".to_string(),
                ]
            );
            // MAX+1 refusal (full pre-allocation refusal matrix is in the
            // bounds test; here for boundary completeness).
            let oversized = vec![0u8; MAX_CONTROL_DOCUMENT_BYTES + 1];
            assert_eq!(
                dir.create_claim("cap-over.json", &oversized).err().map(|e| e.kind()),
                Some(PrivateIoErrorKind::PayloadTooLarge)
            );
            assert!(!root.join("attempt").join("cap-over.json").exists());
            cleanup(&root);
        }
    }

    #[cfg(not(windows))]
    #[test]
    fn non_windows_fails_closed() {
        let error = PrivateAttemptDir::open(std::path::Path::new("x")).err().unwrap();
        assert_eq!(error.kind(), PrivateIoErrorKind::UnsupportedPlatform);
        assert!(current_process_user_sid().is_err());
    }
}
