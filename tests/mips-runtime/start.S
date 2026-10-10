/* Minimal independent o32 ELF entry for a glibc-2.28-linked test executable. */
.set noreorder
.abicalls
.text
.globl _start
.ent _start
_start:
    lui $gp, %hi(_gp)
    addiu $gp, $gp, %lo(_gp)
    move $ra, $zero
    lw $a0, %got(main)($gp)
    lw $a1, 0($sp)
    addiu $a2, $sp, 4
    li $t0, -8
    and $sp, $sp, $t0
    addiu $sp, $sp, -32
    lw $a3, %got(probe_init)($gp)
    lw $t0, %got(probe_fini)($gp)
    sw $t0, 16($sp)
    sw $v0, 20($sp)
    sw $sp, 24($sp)
    lw $t9, %call16(__libc_start_main)($gp)
    jalr $t9
    nop
1:  b 1b
    nop
.end _start
.section .note.GNU-stack,"",@progbits
