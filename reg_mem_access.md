
Currently, pssc generates per-register accessors: read, write, and masked.
When a field is referenced for a bitmask, the literal is shown.

I'd like to eliminate the specific functions, replacing them with 
generic functions.

For example:

REGXYZ_write(self, 1);

write32(&self->regs.REGXYZ, 1);

We should be able to do the same for a masked write:

write32_masked(&self->regs.REGXYZ, 1, 0);


In addition, I'd like to generate the register structs to a separate
file that is only included in the .c file. 

Now, let's think about memory access mechanics. Let's assume that 
all accesses are 'raw' -- in other words, the write/read function
do not have access to 'self'. Let's provide a macro to allow 
someone to customize the access:

```
#ifdef HAVE_PSSC_MMIO_H
#include "pssc_mmio.h" // This is user-provided
#else
static inline void pssc_write32(...) {
  // ...
} 
// ...
#endif
```

In other words, if the user wishes to provide these primitives, they
do so via a header file. Otherwise, we default to local functions
that directly access memory (ie they should inline nicely)


## Masking

It would be nice to have more readable ways to refer to register
offsets. Specifically, I'd like to generate structs with const-static
initialization.

```
struct REG1_s : std_pkg::packed_s<> {
  bit    F1;
  bit[7] F2;
}
```

```
typedef struct field_const_s {
  uint32_t off;
  uint32_t msk;
}

static const struct {
  field_const_s F1;
  field_const_s F2;
} REG1_s_fields = {
  .F1 = {.off=0, .msk=1},
  .F2 = {.off=1, .msk=0xFE}
};
```

Actually, it might be better to use macros -- same readability, 
better folding. Either way, we get more-accessible code




