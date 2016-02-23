#include <algorithm>
#include <future>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <type_traits>
#include <unordered_set>
#include <vector>

#include <boost/filesystem.hpp>
#include <boost/format.hpp>

#include <fcntl.h>
#include <openssl/md5.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace fs = boost::filesystem;

namespace file {

std::string md5sum(const fs::path& path) {
  auto file_descript = open(path.string().c_str(), O_RDONLY);
  if (file_descript < 0) {
    std::ostringstream os;
    auto abspath = fs::current_path() / path;
    os << "failed to open file: " << abspath;
    throw std::runtime_error(os.str());
  }

  auto file_size = fs::file_size(path);
  auto file_buffer = reinterpret_cast<unsigned char*>(
      mmap(0, file_size, PROT_READ, MAP_SHARED, file_descript, 0));
  close(file_descript);

  unsigned char md5[MD5_DIGEST_LENGTH];
  MD5(file_buffer, file_size, md5);
  munmap(file_buffer, file_size);

  std::ostringstream os;
  os << std::hex << std::setfill('0');

  for (int i = 0; i < MD5_DIGEST_LENGTH; ++i)
    os << std::setw(2) << static_cast<int>(md5[i]);

  return os.str();
}


//
// Walk the files in a filesystem, applying unwary op to each regular
// file, starting at root.
//
template<typename UnaryOp>
void walk_files(const fs::path& root, UnaryOp op,
                const bool follow_symlinks = false) {
  if (fs::is_symlink(root) && !follow_symlinks)
    return;

  if (!fs::exists(root)) {
    std::cerr << "warning: " << root << " not found.\n";
    return;
  }

  if (fs::is_regular_file(root)) {
    op(root);
  } else if (fs::is_directory(root)) {
    for (const auto& entry : fs::directory_iterator(root))
      // Recurse into directories in a new thread.
      if (fs::is_directory(entry.path())) {
        std::async(std::launch::async, [&]() {
            walk_files(entry.path(), op);
          });
      } else {
        walk_files(entry.path(), op);
      }
  } else {
    std::cerr << "I don't know what type of file " << root << " is.\n";
  }
}

}  // namespace file

auto get_files_in_dir(const fs::path& root) {
  // Get the current directory
  char currwd[FILENAME_MAX];
  getcwd(currwd, sizeof(currwd));

  // Change to the root directory
  if (chdir(root.string().c_str()))
    throw std::runtime_error("chrdir()");

  std::vector<const fs::path> files;
  file::walk_files(".", [&files](const auto& path){ files.push_back(path); });

  // Return to working directory;
  chdir(currwd);

  return files;
}

bool files_are_identical(const fs::path& lhs, const fs::path& rhs) {
  return fs::file_size(lhs) == fs::file_size(rhs)
      && file::md5sum(lhs) == file::md5sum(rhs);
}


//
// Print the differences between the contents of two contents:
//
// * If a file exists only within the lhs directory, print
//   "+ <filename>".
// * If a file exists only with the rhs directory, print
//   "- <filename>".
// * If a file with the same name and contents in both directories,
//   print "= <filename>".
// * If a file with the same name but different contents exists in
//   both directories, print "M <filename>".
//
void dir_diff(const fs::path& lhs, const fs::path& rhs) {
  const auto leftfiles = get_files_in_dir(lhs),
            rightfiles = get_files_in_dir(rhs);
  auto left = leftfiles.begin(),
      right = rightfiles.begin();

  while (left != leftfiles.end() && right != rightfiles.end()) {
    if (*left == *right) {
      if (files_are_identical(lhs / *left, rhs / *right)) {
        std::cout << "= " << (*left).string() << '\n';
      } else {
        //
        // Files are different. Copy left -> right.
        //
        std::cout << "M " << (*left).string() << '\n';
      }
      ++left; ++right;
    } else if (left < right) {
      //
      // File only exists on left. Copy left -> right.
      //
      std::cout << "+ " << (*left).string() << '\n';
      ++left;
    } else {
      //
      // File only exists on right. Delete right.
      //
      std::cout << "- " << (*right).string() << '\n';
      ++right;
    }
  }

  //
  // Files which only exist on left. Copy left -> right.
  //
  while (left != leftfiles.end())
    std::cout << "+ " << (*left++).string() << '\n';

  //
  // Files which only exist on right. Delete right.
  //
  while (right != rightfiles.end())
    std::cout << "- " << (*right++).string() << '\n';
}


//
// Recursively print the md5sum of every file in directory and
// sub directories, starting at root.
//
void print_dir_md5sums(const fs::path& root) {
  //
  // Our file operator. Prints the checksum and path.
  //
  auto op = [](const auto& path) {
    try {
      std::cout << file::md5sum(path) << ' ' << path.string() << std::endl;
    } catch (std::runtime_error& e) {
      std::cerr << "error: failed to open " << path.string() << std::endl;
    }
  };

  file::walk_files(root, op, false);
}


int main(int argc, char** argv) {
  if (argc == 1) {
    print_dir_md5sums(".");
  } else if (argc == 3) {
    dir_diff(argv[1], argv[2]);
  } else {
    for (auto i = 1; i < argc; i++)
      print_dir_md5sums(argv[i]);
  }

  return 0;
}