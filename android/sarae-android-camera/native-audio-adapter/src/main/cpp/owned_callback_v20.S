// Owned metadata function. Same first three instructions and BL position as
// the pinned callback, followed by owned continuation/helper code only.
.text
.p2align 4
.global sarae_owned_callback_v20
.type sarae_owned_callback_v20,%function
sarae_owned_callback_v20:
    str x20,[sp,#-32]!
    stp x19,x30,[sp,#16]
    add x19,x1,#0x44
    bl .Lowned_entry_helper
    ldr x0,[sp,#8]
    mov x1,x20
    bl sarae_owned_body_v20
    ldp x19,x30,[sp,#16]
    b .Lowned_exit_helper
.size sarae_owned_callback_v20,.-sarae_owned_callback_v20
.Lowned_entry_helper:
    str x0,[sp,#8]
    mov x0,x19
    mov x20,x1
    ret
.Lowned_exit_helper:
    ldr x20,[sp],#32
    ret

.global sarae_owned_checked_v20
.type sarae_owned_checked_v20,%function
sarae_owned_checked_v20:
    stp x19,x20,[sp,#-32]!
    str x30,[sp,#16]
    mov x19,#0x1919
    mov x20,#0x2020
    bl sarae_owned_callback_v20
    mov x8,#0x1919
    cmp x19,x8
    cset w0,ne
    mov x8,#0x2020
    cmp x20,x8
    cset w8,ne
    orr w0,w0,w8,lsl #1
    ldr x30,[sp,#16]
    ldp x19,x20,[sp],#32
    ret
.size sarae_owned_checked_v20,.-sarae_owned_checked_v20

// B-at-entry forces branch relocation even for a four-byte entry patch.
.p2align 4
.global sarae_owned_branch_v20
.type sarae_owned_branch_v20,%function
sarae_owned_branch_v20:
    b .Lowned_branch_helper
    nop
    nop
    nop
.size sarae_owned_branch_v20,.-sarae_owned_branch_v20
.Lowned_branch_helper:
    add x0,x0,#7
    ret
