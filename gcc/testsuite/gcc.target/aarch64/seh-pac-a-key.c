/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -mbranch-protection=pac-ret" } */

extern int consume_one (int);

int
seh_pac_a_key (int x) /* { dg-message "sorry, unimplemented: Windows SEH does not support A-key return address signing" } */
{
  return consume_one (x) + 1;
}
